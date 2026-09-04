"""Fine-tune one target-aware relevance reranker shared by every company.

Confirmed database labels are used for training with whole companies held out for
generalisation checks. A disjoint training portion of the hand-reviewed CSV reduces
pseudo-label noise; its validation/test portions calibrate and evaluate the model.
The resulting artifact is registered as a candidate only.
"""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import random
import re

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ArticleLabel, Company, CompanyKeyword, RawNewsArticle
from app.services.company_reranker import (
    RERANKER_INPUT_SCHEMA,
    build_article_passage,
    build_company_query,
    clean_text,
)
from app.training.common import dataset_hash, register_candidate, version_stamp


LABELS = {"irrelevant": 0, "relevant": 1}
CONFIRMED_STATUSES = {"confirmed", "adjudicated"}
_CSV_COMPANY_PREFIX_RE = re.compile(r"^\s*\[[^\]]+\]\s*")


def _binary_label(value: str) -> int | None:
    if value == "relevant":
        return 1
    if value in {"incidental", "irrelevant"}:
        return 0
    return None


def _load_database_rows() -> list[dict]:
    """Load one highest-priority confirmed label per company/raw article pair."""
    with SessionLocal() as db:
        result_rows = list(
            db.execute(
                select(ArticleLabel, Company, RawNewsArticle)
                .join(Company, Company.id == ArticleLabel.company_id)
                .join(RawNewsArticle, RawNewsArticle.id == ArticleLabel.raw_article_id)
                .where(
                    ArticleLabel.status.in_(CONFIRMED_STATUSES),
                    ArticleLabel.relevance_label.in_(("relevant", "incidental", "irrelevant")),
                )
                .order_by(ArticleLabel.reviewed_at, ArticleLabel.id)
            )
        )
        company_ids = {company.id for _label, company, _raw in result_rows}
        keyword_rows = list(
            db.execute(
                select(
                    CompanyKeyword.company_id,
                    CompanyKeyword.keyword_type,
                    CompanyKeyword.value,
                ).where(CompanyKeyword.company_id.in_(company_ids))
            )
        ) if company_ids else []

    keyword_map: dict[int, dict[str, list[str]]] = {
        company_id: {"alias": [], "product": []} for company_id in company_ids
    }
    for company_id, kind, value in keyword_rows:
        if kind in {"alias", "product"}:
            keyword_map[company_id][kind].append(value)

    # A later adjudicated label wins over a confirmed label, then recency wins.
    selected: dict[tuple[str, int], tuple[ArticleLabel, Company, RawNewsArticle]] = {}
    for label, company, raw in result_rows:
        key = (company.normalized_name or company.name.casefold(), raw.id)
        current = selected.get(key)
        if current is None:
            selected[key] = (label, company, raw)
            continue
        current_label = current[0]
        current_priority = (current_label.status == "adjudicated", current_label.reviewed_at, current_label.id)
        candidate_priority = (label.status == "adjudicated", label.reviewed_at, label.id)
        if candidate_priority >= current_priority:
            selected[key] = (label, company, raw)

    rows: list[dict] = []
    for label, company, raw in selected.values():
        target = _binary_label(label.relevance_label)
        if target is None:
            continue
        keywords = keyword_map.get(company.id, {"alias": [], "product": []})
        rows.append(
            {
                "company": clean_text(company.name),
                "company_group": clean_text(company.normalized_name or company.name).casefold(),
                "aliases": keywords["alias"],
                "products": keywords["product"],
                "title": clean_text(raw.title),
                "summary": clean_text(raw.summary),
                "label": target,
                "raw_article_id": raw.id,
                "reviewed_at": label.reviewed_at,
                "source": "confirmed_db",
            }
        )
    return rows


def _load_human_rows(csv_path: Path) -> list[dict]:
    """Load the manually reviewed CSV as external gold validation/test data."""
    with csv_path.open(encoding="utf-8-sig") as handle:
        raw_rows = list(csv.DictReader(handle))
    rows: list[dict] = []
    for index, row in enumerate(raw_rows):
        if row.get("label") not in LABELS or not row.get("text", "").strip():
            continue
        company = clean_text(row.get("company"))
        article_text = _CSV_COMPANY_PREFIX_RE.sub("", row["text"], count=1)
        rows.append(
            {
                "company": company,
                "company_group": company.casefold(),
                "aliases": [],
                "products": [],
                "title": "",
                "summary": clean_text(article_text),
                "label": LABELS[row["label"]],
                "raw_article_id": f"human:{index}",
                "reviewed_at": datetime.min,
                "source": "human_csv",
            }
        )
    return rows


def company_holdout_split(
    rows: list[dict],
    *,
    seed: int = 29,
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
) -> dict[str, list[dict]]:
    """Keep every normalized company in exactly one split to measure generalisation."""
    companies = sorted({row["company_group"] for row in rows})
    if len(companies) < 3:
        raise ValueError("기업 단위 학습/검증/테스트 분리에는 최소 3개 기업이 필요합니다.")
    best: dict[str, list[dict]] | None = None
    for attempt in range(200):
        shuffled = companies[:]
        random.Random(seed + attempt).shuffle(shuffled)
        validation_count = max(1, round(len(shuffled) * validation_ratio))
        test_count = max(1, len(shuffled) - round(len(shuffled) * (train_ratio + validation_ratio)))
        if validation_count + test_count >= len(shuffled):
            validation_count = 1
            test_count = 1
        train_end = len(shuffled) - validation_count - test_count
        validation_end = train_end + validation_count
        groups = {
            "train": set(shuffled[:train_end]),
            "validation": set(shuffled[train_end:validation_end]),
            "test": set(shuffled[validation_end:]),
        }
        candidate = {
            split: [row for row in rows if row["company_group"] in names]
            for split, names in groups.items()
        }
        best = candidate
        if all({row["label"] for row in items} == {0, 1} for items in candidate.values()):
            return candidate
    assert best is not None
    return best


def _stratified_human_split(rows: list[dict], seed: int = 41) -> dict[str, list[dict]]:
    """Create disjoint 70/15/15 human-label train, calibration and test sets."""
    rng = random.Random(seed)
    output = {"train": [], "validation": [], "test": []}
    by_label: dict[int, list[dict]] = {0: [], 1: []}
    for row in rows:
        by_label[row["label"]].append(row)
    for items in by_label.values():
        shuffled = items[:]
        rng.shuffle(shuffled)
        train_end = max(1, int(len(shuffled) * 0.70))
        validation_end = max(train_end + 1, int(len(shuffled) * 0.85))
        output["train"].extend(shuffled[:train_end])
        output["validation"].extend(shuffled[train_end:validation_end])
        output["test"].extend(shuffled[validation_end:])
    for items in output.values():
        rng.shuffle(items)
    return output


def calibrate_thresholds(targets: list[int], scores: list[float]) -> dict[str, float]:
    """Create a high-precision accept band and a high-NPV reject band."""
    from sklearn.metrics import precision_recall_fscore_support

    if not targets or len(set(targets)) < 2:
        return {"accept": 0.70, "reject": 0.30}
    candidates = [value / 100 for value in range(5, 96)]
    accept_rows: list[tuple[float, float, float]] = []
    f1_rows: list[tuple[float, float]] = []
    for threshold in candidates:
        predictions = [int(score >= threshold) for score in scores]
        precision, recall, f1, _ = precision_recall_fscore_support(
            targets, predictions, average="binary", zero_division=0
        )
        f1_rows.append((float(f1), threshold))
        if precision >= 0.80:
            accept_rows.append((float(recall), float(precision), threshold))
    accept = max(accept_rows)[2] if accept_rows else max(f1_rows)[1]

    reject_rows: list[tuple[int, float]] = []
    for threshold in candidates:
        if threshold >= accept:
            continue
        rejected = [target for target, score in zip(targets, scores) if score <= threshold]
        if rejected:
            negative_precision = sum(target == 0 for target in rejected) / len(rejected)
            if negative_precision >= 0.95:
                reject_rows.append((len(rejected), threshold))
    reject = max(reject_rows)[1] if reject_rows else min(0.30, max(0.05, accept - 0.20))
    return {"accept": round(float(accept), 4), "reject": round(float(reject), 4)}


def _metrics(targets: list[int], scores: list[float], threshold: float) -> dict:
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    if not targets:
        return {"total": 0}
    predictions = [int(score >= threshold) for score in scores]
    result = {
        "total": len(targets),
        "accuracy": float(accuracy_score(targets, predictions)),
        "f1_relevant": float(f1_score(targets, predictions, pos_label=1, zero_division=0)),
        "precision_relevant": float(precision_score(targets, predictions, pos_label=1, zero_division=0)),
        "recall_relevant": float(recall_score(targets, predictions, pos_label=1, zero_division=0)),
        "precision_irrelevant": float(precision_score(targets, predictions, pos_label=0, zero_division=0)),
        "recall_irrelevant": float(recall_score(targets, predictions, pos_label=0, zero_division=0)),
        "pr_auc": float(average_precision_score(targets, scores)),
        "confusion_matrix": confusion_matrix(targets, predictions, labels=[0, 1]).tolist(),
    }
    if len(set(targets)) == 2:
        result["roc_auc"] = float(roc_auc_score(targets, scores))
    return result


def train_company_reranker(
    output_root: Path,
    human_csv: Path,
    base_model: str = "BAAI/bge-reranker-v2-m3",
    epochs: int = 2,
    batch_size: int = 2,
    seed: int = 29,
    human_weight: int = 1,
) -> dict:
    """Train, evaluate, save and register a non-production candidate reranker."""
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.manual_seed(seed)
    database_rows = _load_database_rows()
    human_rows = _load_human_rows(human_csv)
    # If a hand-reviewed row also exists in the auto-labelled DB, keep it out of
    # training so the external human metrics remain a true holdout.
    human_article_keys = {
        clean_text(f"{row['title']} {row['summary']}").casefold() for row in human_rows
    }
    database_rows = [
        row
        for row in database_rows
        if clean_text(f"{row['title']} {row['summary']}").casefold() not in human_article_keys
    ]
    if len(database_rows) < 1500:
        raise ValueError(f"학습 가능한 DB 관련성 라벨 {len(database_rows)}건: 최소 1,500건 필요")
    if len(human_rows) < 100:
        raise ValueError(f"사람 검증 라벨 {len(human_rows)}건: 최소 100건 필요")
    db_splits = company_holdout_split(database_rows, seed=seed)
    human_splits = _stratified_human_split(human_rows, seed=seed + 12)
    db_train_companies = {row["company_group"] for row in db_splits["train"]}
    # Keep the DB validation/test company names genuinely unseen. Human rows for
    # those names are moved out of training and into calibration.
    held_out_human_train = [
        row for row in human_splits["train"]
        if row["company_group"] not in db_train_companies
    ]
    human_splits["train"] = [
        row for row in human_splits["train"]
        if row["company_group"] in db_train_companies
    ]
    human_splits["validation"].extend(held_out_human_train)
    human_weight = max(1, human_weight)
    training_rows = [
        *db_splits["train"],
        *(human_splits["train"] * human_weight),
    ]

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)

    class PairDataset(Dataset):
        def __init__(self, items: list[dict]):
            self.items = items

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            item = self.items[index]
            encoded = tokenizer(
                build_company_query(item["company"], item["aliases"], item["products"]),
                build_article_passage(item["title"], item["summary"]),
                truncation=True,
                max_length=512,
                padding="max_length",
                return_tensors="pt",
            )
            return {
                **{key: value.squeeze(0) for key, value in encoded.items()},
                "labels": torch.tensor(float(item["label"]), dtype=torch.float32),
            }

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=1,
        trust_remote_code=True,
    ).to(device)
    model.config.id2label = {0: "relevant"}
    model.config.label2id = {"relevant": 0}
    if hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable()
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False

    label_counts = Counter(row["label"] for row in training_rows)
    positive_weight = max(1.0, label_counts[0] / max(1, label_counts[1]))
    loss_fn = torch.nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([positive_weight], device=device)
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=0.01)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    train_loader = DataLoader(
        PairDataset(training_rows),
        batch_size=max(1, batch_size),
        shuffle=True,
    )
    for epoch in range(max(1, epochs)):
        model.train()
        running_loss = 0.0
        for step, batch in enumerate(train_loader, start=1):
            labels = batch.pop("labels").to(device)
            inputs = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(**inputs).logits.view(-1)
                loss = loss_fn(logits, labels)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            running_loss += float(loss.detach().cpu())
            if step % 100 == 0 or step == len(train_loader):
                print(
                    json.dumps(
                        {
                            "stage": "training",
                            "epoch": epoch + 1,
                            "epochs": max(1, epochs),
                            "step": step,
                            "steps": len(train_loader),
                            "mean_loss": round(running_loss / step, 6),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    def score(items: list[dict]) -> tuple[list[int], list[float]]:
        targets: list[int] = []
        values: list[float] = []
        model.eval()
        for batch in DataLoader(PairDataset(items), batch_size=max(2, batch_size * 2)):
            targets.extend(int(item) for item in batch.pop("labels").tolist())
            inputs = {key: value.to(device) for key, value in batch.items()}
            with torch.no_grad(), torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(**inputs).logits.view(-1)
            values.extend(float(item) for item in torch.sigmoid(logits).cpu().tolist())
        return targets, values

    human_validation_targets, human_validation_scores = score(human_splits["validation"])
    thresholds = calibrate_thresholds(human_validation_targets, human_validation_scores)
    metrics: dict[str, dict] = {
        "human_validation": _metrics(
            human_validation_targets, human_validation_scores, thresholds["accept"]
        )
    }
    for name, items in (
        ("unseen_company_validation", db_splits["validation"]),
        ("unseen_company_test", db_splits["test"]),
        ("human_test", human_splits["test"]),
    ):
        targets, values = score(items)
        metrics[name] = _metrics(targets, values, thresholds["accept"])

    version = version_stamp("company-reranker")
    output = output_root / version
    output.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(output)
    tokenizer.save_pretrained(output)
    metadata = {
        "input_schema": RERANKER_INPUT_SCHEMA,
        "base_model": base_model,
        "max_length": 512,
        "thresholds": thresholds,
        "training_seed": seed,
        "human_csv": human_csv.name,
        "human_training_weight": human_weight,
    }
    (output / "company_reranker_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    counts = {
        name: {
            "total": len(items),
            "relevant": sum(row["label"] == 1 for row in items),
            "irrelevant_or_incidental": sum(row["label"] == 0 for row in items),
            "companies": len({row["company_group"] for row in items}),
        }
        for name, items in {
            **db_splits,
            "human_train": human_splits["train"],
            "human_validation": human_splits["validation"],
            "human_test": human_splits["test"],
        }.items()
    }
    hash_rows = [
        {
            "company": row["company_group"],
            "article": row["raw_article_id"],
            "label": row["label"],
            "source": row["source"],
        }
        for row in [*database_rows, *human_rows]
    ]
    registry = register_candidate(
        task="company_relevance_reranker",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(hash_rows),
        label_schema={"relevance": LABELS, "incidental_policy": "irrelevant"},
        metrics=metrics,
        thresholds=thresholds,
        training_counts=counts,
        base_model=base_model,
        dependencies={
            "input_schema": RERANKER_INPUT_SCHEMA,
            "human_validation_csv": human_csv.name,
            "human_training_weight": human_weight,
            "split_policy": "company-disjoint-db; disjoint-human-70/15/15",
        },
    )
    return {
        "model_version_id": registry.id,
        "status": registry.status,
        "version": version,
        "artifact_path": str(output),
        "thresholds": thresholds,
        "metrics": metrics,
        "counts": counts,
    }
