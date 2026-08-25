"""Fine-tune one multi-head KLUE filter and a separate three-class sentiment model."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ArticleLabel, NewsArticle, RawNewsArticle, StoryClusterArticle
from app.training.common import chronological_group_split, dataset_hash, register_candidate, version_stamp


RELEVANCE = {"relevant": 0, "incidental": 1, "irrelevant": 2}
ADVERTISEMENT = {"no": 0, "yes": 1}
SENTIMENT = {"positive": 0, "neutral": 1, "negative": 2}


@dataclass
class TextRow:
    label_id: int
    text: str
    published_at: datetime
    group: str
    relevance: int = -100
    advertisement: int = -100
    sentiment: int = -100


def load_rows() -> list[TextRow]:
    """Load only explicit confirmed/adjudicated PostgreSQL labels, never legacy SQLite reviews."""
    with SessionLocal() as db:
        records = db.execute(
            select(ArticleLabel, RawNewsArticle, StoryClusterArticle.story_cluster_id)
            .join(RawNewsArticle, RawNewsArticle.id == ArticleLabel.raw_article_id)
            .outerjoin(NewsArticle, NewsArticle.raw_article_id == RawNewsArticle.id)
            .outerjoin(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
            .where(ArticleLabel.status.in_(["confirmed", "adjudicated"]))
            .order_by(RawNewsArticle.published_at, ArticleLabel.id)
        ).all()
    rows: list[TextRow] = []
    for label, raw, story_id in records:
        rows.append(
            TextRow(
                label_id=label.id,
                text=" ".join(filter(None, [raw.title, raw.summary]))[:6000],
                published_at=raw.published_at or raw.collected_at or datetime.now(timezone.utc),
                group=f"story:{story_id}" if story_id is not None else f"content:{raw.content_hash}",
                relevance=RELEVANCE.get(label.relevance_label, -100),
                advertisement=ADVERTISEMENT.get(label.advertisement_label, -100),
                sentiment=SENTIMENT.get(label.sentiment_label, -100),
            )
        )
    return rows


def _filter_eligible_rows(rows: list[TextRow]) -> list[TextRow]:
    """Drop reviews where both filter heads are intentionally masked."""
    return [
        row
        for row in rows
        if row.relevance >= 0 or row.advertisement >= 0
    ]


def _validate_filter_rows(rows: list[TextRow]) -> tuple[list[TextRow], dict]:
    eligible = _filter_eligible_rows(rows)
    counts = {
        "total": len(eligible),
        "relevance": Counter(row.relevance for row in eligible if row.relevance >= 0),
        "advertisement": Counter(
            row.advertisement for row in eligible if row.advertisement >= 0
        ),
    }
    if len(eligible) < 1500:
        raise ValueError(
            f"학습 가능한 확정 기사 라벨 {len(eligible)}/1500건: "
            "필터 미세튜닝 기준 미달"
        )
    for name in ("relevance", "advertisement"):
        if not counts[name] or min(counts[name].values()) < 100:
            raise ValueError(f"{name} 각 클래스 100건 기준 미달: {dict(counts[name])}")
    return eligible, {
        key: dict(value) if isinstance(value, Counter) else value
        for key, value in counts.items()
    }


def _validate_sentiment_rows(rows: list[TextRow]) -> tuple[list[TextRow], dict]:
    eligible = [row for row in rows if row.sentiment >= 0]
    counts = Counter(row.sentiment for row in eligible)
    if len(eligible) < 1500:
        raise ValueError(
            f"학습 가능한 확정 감성 라벨 {len(eligible)}/1500건: 감성 미세튜닝 기준 미달"
        )
    if len(counts) < 3 or min(counts.values(), default=0) < 100:
        raise ValueError(f"감성 각 클래스 100건 기준 미달: {dict(counts)}")
    return eligible, {"total": len(eligible), "sentiment": dict(counts)}


def _macro_f1(targets: list[int], predictions: list[int]) -> float:
    from sklearn.metrics import f1_score

    return float(f1_score(targets, predictions, average="macro", zero_division=0))


def _classification_metrics(
    targets: list[int],
    predictions: list[int],
    probabilities: list[list[float]],
    labels: dict[str, int],
) -> dict[str, float]:
    """Report macro-F1, macro PR-AUC and each sparse class recall."""
    if not targets:
        return {"macro_f1": 0.0, "macro_pr_auc": 0.0}
    import numpy as np
    from sklearn.metrics import average_precision_score, f1_score, recall_score

    result = {
        "macro_f1": float(f1_score(targets, predictions, average="macro", zero_division=0)),
    }
    try:
        one_hot = np.eye(len(labels), dtype=int)[np.asarray(targets, dtype=int)]
        result["macro_pr_auc"] = float(
            average_precision_score(one_hot, np.asarray(probabilities), average="macro")
        )
    except ValueError:
        result["macro_pr_auc"] = 0.0
    recalls = recall_score(
        targets,
        predictions,
        labels=list(range(len(labels))),
        average=None,
        zero_division=0,
    )
    for label, index in labels.items():
        result[f"recall_{label}"] = float(recalls[index])
    return result


def train_filter(output_root: Path, base_model: str = "klue/roberta-base", epochs: int = 4) -> dict:
    """Train shared encoder + independent relevance/ad heads with masked uncertain labels."""
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModel, AutoTokenizer

    rows, counts = _validate_filter_rows(load_rows())
    splits = chronological_group_split(
        rows,
        group_key=lambda row: row.group,
        time_key=lambda row: row.published_at,
    )
    tokenizer = AutoTokenizer.from_pretrained(base_model)

    class ReviewDataset(Dataset):
        def __init__(self, items: list[TextRow]):
            self.items = items

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            item = self.items[index]
            encoded = tokenizer(
                item.text,
                truncation=True,
                max_length=384,
                padding="max_length",
                return_tensors="pt",
            )
            return {
                "input_ids": encoded["input_ids"].squeeze(0),
                "attention_mask": encoded["attention_mask"].squeeze(0),
                "relevance": torch.tensor(item.relevance),
                "advertisement": torch.tensor(item.advertisement),
            }

    class FilterModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = AutoModel.from_pretrained(base_model)
            hidden = self.encoder.config.hidden_size
            self.dropout = nn.Dropout(0.1)
            self.relevance_head = nn.Linear(hidden, 3)
            self.advertisement_head = nn.Linear(hidden, 2)

        def forward(self, input_ids, attention_mask):
            output = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
            pooled = self.dropout(output.last_hidden_state[:, 0])
            return self.relevance_head(pooled), self.advertisement_head(pooled)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = FilterModel().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, weight_decay=0.01)
    criterion = nn.CrossEntropyLoss()
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    train_loader = DataLoader(ReviewDataset(splits["train"]), batch_size=8, shuffle=True)
    for _ in range(epochs):
        model.train()
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                relevance, advertisement = model(
                    batch["input_ids"].to(device), batch["attention_mask"].to(device)
                )
                relevance_targets = batch["relevance"].to(device)
                advertisement_targets = batch["advertisement"].to(device)
                losses = []
                relevance_mask = relevance_targets >= 0
                advertisement_mask = advertisement_targets >= 0
                if relevance_mask.any():
                    losses.append(criterion(relevance[relevance_mask], relevance_targets[relevance_mask]))
                if advertisement_mask.any():
                    losses.append(
                        criterion(
                            advertisement[advertisement_mask],
                            advertisement_targets[advertisement_mask],
                        )
                    )
                if not losses:
                    continue
                loss = torch.stack(losses).sum()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

    metrics: dict[str, float] = {}
    model.eval()
    for split_name in ("validation", "test"):
        rel_y: list[int] = []
        rel_p: list[int] = []
        rel_probabilities: list[list[float]] = []
        ad_y: list[int] = []
        ad_p: list[int] = []
        ad_probabilities: list[list[float]] = []
        for batch in DataLoader(ReviewDataset(splits[split_name]), batch_size=16):
            with torch.no_grad():
                relevance, advertisement = model(
                    batch["input_ids"].to(device), batch["attention_mask"].to(device)
                )
            relevance_probs = relevance.softmax(-1).cpu().tolist()
            advertisement_probs = advertisement.softmax(-1).cpu().tolist()
            for target, prediction, probabilities in zip(batch["relevance"].tolist(), relevance.argmax(-1).cpu().tolist(), relevance_probs):
                if target >= 0:
                    rel_y.append(target); rel_p.append(prediction); rel_probabilities.append(probabilities)
            for target, prediction, probabilities in zip(batch["advertisement"].tolist(), advertisement.argmax(-1).cpu().tolist(), advertisement_probs):
                if target >= 0:
                    ad_y.append(target); ad_p.append(prediction); ad_probabilities.append(probabilities)
        for key, value in _classification_metrics(rel_y, rel_p, rel_probabilities, RELEVANCE).items():
            metrics[f"{split_name}_relevance_{key}"] = value
        for key, value in _classification_metrics(ad_y, ad_p, ad_probabilities, ADVERTISEMENT).items():
            metrics[f"{split_name}_advertisement_{key}"] = value

    version = version_stamp("article-filter")
    output = output_root / version
    output.mkdir(parents=True, exist_ok=False)
    tokenizer.save_pretrained(output)
    torch.save(model.state_dict(), output / "filter_state.pt")
    metadata = {
        "base_model": base_model,
        "relevance_labels": RELEVANCE,
        "advertisement_labels": ADVERTISEMENT,
        "max_length": 384,
    }
    (output / "filter_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    data_rows = [{"id": row.label_id, "group": row.group, "r": row.relevance, "a": row.advertisement} for row in rows]
    registry = register_candidate(
        task="article_filter",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(data_rows),
        label_schema={"relevance": RELEVANCE, "advertisement": ADVERTISEMENT},
        metrics=metrics,
        thresholds={"relevance_accept": 0.70, "relevance_reject": 0.30, "advertisement_reject": 0.85},
        training_counts=counts,
        base_model=base_model,
    )
    return {"model_version_id": registry.id, "version": version, "metrics": metrics, "counts": counts}


def train_sentiment(output_root: Path, base_model: str = "klue/roberta-base", epochs: int = 4) -> dict:
    """Train the independent positive/neutral/negative KLUE classifier."""
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rows, counts = _validate_sentiment_rows(load_rows())
    splits = chronological_group_split(rows, group_key=lambda row: row.group, time_key=lambda row: row.published_at)
    tokenizer = AutoTokenizer.from_pretrained(base_model)

    class SentimentDataset(Dataset):
        def __init__(self, items): self.items = items
        def __len__(self): return len(self.items)
        def __getitem__(self, index):
            item = self.items[index]
            encoded = tokenizer(item.text, truncation=True, max_length=384, padding="max_length", return_tensors="pt")
            return {
                "input_ids": encoded["input_ids"].squeeze(0),
                "attention_mask": encoded["attention_mask"].squeeze(0),
                "labels": torch.tensor(item.sentiment),
            }

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=3,
        id2label={value: key for key, value in SENTIMENT.items()},
        label2id=SENTIMENT,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, weight_decay=0.01)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    for _ in range(epochs):
        model.train()
        for batch in DataLoader(SentimentDataset(splits["train"]), batch_size=8, shuffle=True):
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                output = model(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                    labels=batch["labels"].to(device),
                )
            scaler.scale(output.loss).backward(); scaler.step(optimizer); scaler.update()
    metrics: dict[str, float] = {}
    model.eval()
    for split_name in ("validation", "test"):
        targets: list[int] = []; predictions: list[int] = []; probabilities: list[list[float]] = []
        for batch in DataLoader(SentimentDataset(splits[split_name]), batch_size=16):
            with torch.no_grad():
                logits = model(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                ).logits
            targets.extend(batch["labels"].tolist()); predictions.extend(logits.argmax(-1).cpu().tolist()); probabilities.extend(logits.softmax(-1).cpu().tolist())
        for key, value in _classification_metrics(targets, predictions, probabilities, SENTIMENT).items():
            metrics[f"{split_name}_{key}"] = value
    version = version_stamp("sentiment")
    output = output_root / version
    output.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(output); tokenizer.save_pretrained(output)
    data_rows = [{"id": row.label_id, "group": row.group, "s": row.sentiment} for row in rows]
    registry = register_candidate(
        task="sentiment",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(data_rows),
        label_schema={"sentiment": SENTIMENT},
        metrics=metrics,
        thresholds={},
        training_counts=counts,
        base_model=base_model,
    )
    return {"model_version_id": registry.id, "version": version, "metrics": metrics, "counts": counts}
