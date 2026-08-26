"""Fine-tune the single-head normal/filter relevance classifier from a labeled CSV export.

Unlike train_filter() in text_models.py (a dual-head relevance+advertisement model
trained from DB-backed article_labels), this trains the simpler single-head
normal/filter classifier that app.services.fine_tuned_text.predict_relevance()
actually loads today (klue_roberta_spam_finetuned). The CSV has no company_id/
raw_article_id, so it cannot go through article_labels; it is consumed directly.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import random

from app.training.common import dataset_hash, register_candidate, version_stamp


RELEVANCE = {"normal": 0, "filter": 1}


def _load_csv_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if row.get("text", "").strip() and row.get("llm_label") in RELEVANCE]
    return rows


def _stratified_split(
    rows: list[dict],
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
    seed: int = 42,
) -> dict[str, list[dict]]:
    """Split within each label separately so train/validation/test share the same class balance."""
    by_label: dict[str, list[dict]] = {}
    for row in rows:
        by_label.setdefault(row["llm_label"], []).append(row)
    rng = random.Random(seed)
    splits: dict[str, list[dict]] = {"train": [], "validation": [], "test": []}
    for items in by_label.values():
        shuffled = items[:]
        rng.shuffle(shuffled)
        total = len(shuffled)
        train_end = max(1, int(total * train_ratio))
        validation_end = max(train_end + 1, int(total * (train_ratio + validation_ratio)))
        splits["train"].extend(shuffled[:train_end])
        splits["validation"].extend(shuffled[train_end:validation_end])
        splits["test"].extend(shuffled[validation_end:])
    for items in splits.values():
        rng.shuffle(items)
    return splits


def _classification_metrics(targets: list[int], predictions: list[int], probabilities: list[list[float]]) -> dict:
    if not targets:
        return {"macro_f1": 0.0}
    import numpy as np
    from sklearn.metrics import (
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )

    result = {
        "accuracy": float(np.mean(np.array(targets) == np.array(predictions))),
        "macro_f1": float(f1_score(targets, predictions, average="macro", zero_division=0)),
        "precision_filter": float(precision_score(targets, predictions, pos_label=1, zero_division=0)),
        "recall_filter": float(recall_score(targets, predictions, pos_label=1, zero_division=0)),
        "precision_normal": float(precision_score(targets, predictions, pos_label=0, zero_division=0)),
        "recall_normal": float(recall_score(targets, predictions, pos_label=0, zero_division=0)),
        "confusion_matrix": confusion_matrix(targets, predictions, labels=[0, 1]).tolist(),
    }
    return result


def train_relevance_from_csv(
    csv_path: Path,
    output_root: Path,
    base_model: str = "klue/roberta-base",
    epochs: int = 4,
) -> dict:
    """Fine-tune a normal/filter binary classifier directly from a labeled CSV (no DB dependency)."""
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rows = _load_csv_rows(csv_path)
    if len(rows) < 50:
        raise ValueError(f"학습 가능한 라벨 {len(rows)}건: 최소 50건 필요")
    splits = _stratified_split(rows)
    tokenizer = AutoTokenizer.from_pretrained(base_model)

    class CsvDataset(Dataset):
        def __init__(self, items: list[dict]):
            self.items = items

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            item = self.items[index]
            encoded = tokenizer(
                item["text"][:6000],
                truncation=True,
                max_length=384,
                padding="max_length",
                return_tensors="pt",
            )
            return {
                "input_ids": encoded["input_ids"].squeeze(0),
                "attention_mask": encoded["attention_mask"].squeeze(0),
                "labels": torch.tensor(RELEVANCE[item["llm_label"]]),
            }

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=2,
        id2label={value: key for key, value in RELEVANCE.items()},
        label2id=RELEVANCE,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, weight_decay=0.01)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    for _ in range(epochs):
        model.train()
        for batch in DataLoader(CsvDataset(splits["train"]), batch_size=8, shuffle=True):
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
    counts: dict[str, dict[str, int]] = {}
    for split_name in ("validation", "test"):
        targets: list[int] = []
        predictions: list[int] = []
        probabilities: list[list[float]] = []
        for batch in DataLoader(CsvDataset(splits[split_name]), batch_size=16):
            with torch.no_grad():
                logits = model(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                ).logits
            targets.extend(batch["labels"].tolist())
            predictions.extend(logits.argmax(-1).cpu().tolist())
            probabilities.extend(logits.softmax(-1).cpu().tolist())
        for key, value in _classification_metrics(targets, predictions, probabilities).items():
            metrics[f"{split_name}_{key}"] = value
        counts[split_name] = {"total": len(targets)}
    counts["train"] = {"total": len(splits["train"])}

    version = version_stamp("article-relevance-csv")
    output = output_root / version
    output.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(output)
    tokenizer.save_pretrained(output)
    data_rows = [{"text": row["text"], "label": row["llm_label"]} for row in rows]
    registry = register_candidate(
        task="article_relevance",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(data_rows),
        label_schema={"relevance": RELEVANCE},
        metrics=metrics,
        thresholds={},
        training_counts=counts,
        base_model=base_model,
        dependencies={"source_csv": str(csv_path.name)},
    )
    return {
        "model_version_id": registry.id,
        "version": version,
        "artifact_path": str(output),
        "metrics": metrics,
        "counts": counts,
    }
