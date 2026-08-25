"""Reproducible dataset splitting, hashing, metrics and model registry helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from app.database import SessionLocal
from app.models import ModelVersion


def chronological_group_split(
    rows: list[Any],
    *,
    group_key: Callable[[Any], str],
    time_key: Callable[[Any], datetime],
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
) -> dict[str, list[Any]]:
    """Keep a story/event group intact and assign groups by their earliest timestamp."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        groups[str(group_key(row))].append(row)
    ordered = sorted(
        groups.values(),
        key=lambda items: min(time_key(item) for item in items),
    )
    total = len(ordered)
    train_end = max(1, int(total * train_ratio)) if total else 0
    validation_end = max(train_end + 1, int(total * (train_ratio + validation_ratio))) if total > 2 else train_end
    split_groups = {
        "train": ordered[:train_end],
        "validation": ordered[train_end:validation_end],
        "test": ordered[validation_end:],
    }
    return {
        name: [item for group in grouped_rows for item in group]
        for name, grouped_rows in split_groups.items()
    }


def dataset_hash(rows: list[dict]) -> str:
    """Hash sorted label-bearing records without depending on database row order."""
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def register_candidate(
    *,
    task: str,
    version: str,
    artifact_path: Path,
    training_data_hash: str,
    label_schema: dict,
    metrics: dict,
    thresholds: dict,
    training_counts: dict,
    dependencies: dict | None = None,
    base_model: str | None = None,
) -> ModelVersion:
    """Register an immutable candidate; promotion remains a separate human action."""
    with SessionLocal() as db:
        model = ModelVersion(
            task=task,
            version=version,
            status="candidate",
            base_model=base_model,
            artifact_path=str(artifact_path.resolve()),
            training_data_hash=training_data_hash,
            label_schema=label_schema,
            metrics=metrics,
            thresholds=thresholds,
            training_counts=training_counts,
            dependencies=dependencies or {},
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        return model


def version_stamp(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
