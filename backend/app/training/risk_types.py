"""Fine-tune an optional KLUE/RoBERTa multi-label risk-type classifier."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import NewsArticle, RiskEvent, RiskEventArticle, RiskEventLabel
from app.risk_taxonomy import RISK_TYPES
from app.training.common import chronological_group_split, dataset_hash, register_candidate, version_stamp


@dataclass
class RiskTypeRow:
    event_id: int
    text: str
    occurred_at: datetime
    targets: list[float]


def load_risk_type_rows() -> list[RiskTypeRow]:
    """Load one authoritative, evidence-backed reviewed label per risk event."""
    with SessionLocal() as db:
        labels = list(
            db.scalars(
                select(RiskEventLabel)
                .where(
                    RiskEventLabel.status.in_(("confirmed", "adjudicated")),
                    RiskEventLabel.is_risk.is_(True),
                )
                .order_by(RiskEventLabel.reviewed_at.desc(), RiskEventLabel.id.desc())
            )
        )
        selected: dict[int, RiskEventLabel] = {}
        for label in labels:
            current = selected.get(label.risk_event_id)
            if current is None or (
                label.status == "adjudicated" and current.status != "adjudicated"
            ):
                selected[label.risk_event_id] = label

        rows: list[RiskTypeRow] = []
        for event_id, label in selected.items():
            risk_types = set(label.risk_types or []) & set(RISK_TYPES)
            evidence_ids = list(dict.fromkeys(label.evidence_article_ids or []))
            if not risk_types or not evidence_ids:
                continue
            articles = list(
                db.scalars(
                    select(NewsArticle)
                    .join(RiskEventArticle, RiskEventArticle.article_id == NewsArticle.id)
                    .where(
                        RiskEventArticle.risk_event_id == event_id,
                        NewsArticle.id.in_(evidence_ids),
                    )
                )
            )
            by_id = {article.id: article for article in articles}
            text = " ".join(
                " ".join(filter(None, [by_id[article_id].title, by_id[article_id].summary]))
                for article_id in evidence_ids
                if article_id in by_id
            )[:6000]
            if not text:
                continue
            event = db.get(RiskEvent, event_id)
            occurred_at = label.event_start or (
                event.opened_at if event is not None else datetime.now(timezone.utc)
            )
            rows.append(
                RiskTypeRow(
                    event_id=event_id,
                    text=text,
                    occurred_at=occurred_at,
                    targets=[float(risk_type in risk_types) for risk_type in RISK_TYPES],
                )
            )
    return rows


def validate_risk_type_rows(rows: list[RiskTypeRow]) -> dict:
    """Require enough reviewed events and positive examples for every type."""
    counts = Counter()
    for row in rows:
        for index, value in enumerate(row.targets):
            if value:
                counts[RISK_TYPES[index]] += 1
    if len(rows) < 200:
        raise ValueError(
            f"근거가 확정된 위험 사건 {len(rows)}/200건: 위험 유형 모델 학습 기준 미달"
        )
    missing = {
        risk_type: counts.get(risk_type, 0)
        for risk_type in RISK_TYPES
        if counts.get(risk_type, 0) < 20
    }
    if missing:
        raise ValueError(f"위험 유형별 최소 20건 기준 미달: {missing}")
    return {"events": len(rows), "risk_types": dict(counts)}


def _metrics(targets, probabilities) -> dict[str, float]:
    import numpy as np
    from sklearn.metrics import average_precision_score, f1_score, recall_score

    if not len(targets):
        return {"macro_f1": 0.0, "macro_pr_auc": 0.0}
    targets = np.asarray(targets, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    predictions = probabilities >= 0.5
    result = {
        "macro_f1": float(
            f1_score(targets, predictions, average="macro", zero_division=0)
        )
    }
    eligible = [index for index in range(len(RISK_TYPES)) if targets[:, index].sum()]
    result["macro_pr_auc"] = float(
        average_precision_score(
            targets[:, eligible], probabilities[:, eligible], average="macro"
        )
    ) if eligible else 0.0
    recalls = recall_score(
        targets,
        predictions,
        average=None,
        zero_division=0,
    )
    for index, risk_type in enumerate(RISK_TYPES):
        result[f"recall_{risk_type}"] = float(recalls[index])
    return result


def train_risk_type_classifier(
    output_root: Path,
    base_model: str = "klue/roberta-base",
    epochs: int = 4,
) -> dict:
    """Train a candidate model; production promotion remains a human action."""
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rows = load_risk_type_rows()
    counts = validate_risk_type_rows(rows)
    splits = chronological_group_split(
        rows,
        group_key=lambda row: str(row.event_id),
        time_key=lambda row: row.occurred_at,
    )
    tokenizer = AutoTokenizer.from_pretrained(base_model)

    class RiskTypeDataset(Dataset):
        def __init__(self, items):
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
                "labels": torch.tensor(item.targets, dtype=torch.float32),
            }

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=len(RISK_TYPES),
        id2label={index: risk_type for index, risk_type in enumerate(RISK_TYPES)},
        label2id={risk_type: index for index, risk_type in enumerate(RISK_TYPES)},
        problem_type="multi_label_classification",
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, weight_decay=0.01)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    for _ in range(epochs):
        model.train()
        for batch in DataLoader(RiskTypeDataset(splits["train"]), batch_size=8, shuffle=True):
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                output = model(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                    labels=batch["labels"].to(device),
                )
            scaler.scale(output.loss).backward()
            scaler.step(optimizer)
            scaler.update()

    metrics: dict[str, float] = {}
    model.eval()
    for split_name in ("validation", "test"):
        targets: list[list[float]] = []
        probabilities: list[list[float]] = []
        for batch in DataLoader(RiskTypeDataset(splits[split_name]), batch_size=16):
            with torch.no_grad():
                logits = model(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                ).logits
            targets.extend(batch["labels"].tolist())
            probabilities.extend(torch.sigmoid(logits).cpu().tolist())
        for key, value in _metrics(targets, probabilities).items():
            metrics[f"{split_name}_{key}"] = value

    version = version_stamp("risk-types")
    output = output_root / version
    output.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(output)
    tokenizer.save_pretrained(output)
    registry = register_candidate(
        task="risk_type_classifier",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(
            [
                {"event": row.event_id, "time": row.occurred_at, "targets": row.targets}
                for row in rows
            ]
        ),
        label_schema={"risk_types": list(RISK_TYPES), "problem": "multi_label"},
        metrics=metrics,
        thresholds={"decision": 0.5},
        training_counts=counts,
        base_model=base_model,
    )
    return {
        "model_version_id": registry.id,
        "version": version,
        "metrics": metrics,
        "counts": counts,
    }
