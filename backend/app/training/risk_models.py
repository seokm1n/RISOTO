"""Train one shared Isolation Forest and one shared LightGBM over event-grouped windows."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.database import SessionLocal
from app.models import CompanyFeatureWindow, ModelVersion, RiskEvent, RiskEventLabel
from app.services.risk_analysis import (
    BASE_FEATURE_NAMES,
    REQUIRED_IF_HASH_KEY,
    REQUIRED_IF_VERSION_KEY,
    RISK_DETECTOR_FEATURE_NAMES,
    artifact_sha256,
    isolation_forest_artifact_is_valid,
)
from app.services.risk_ground_truth import authoritative_risk_label
from app.training.common import chronological_group_split, dataset_hash, register_candidate, version_stamp


# Backward-compatible training-module name; serving owns the canonical contract.
RISK_FEATURE_NAMES = RISK_DETECTOR_FEATURE_NAMES


def _if_training_windows(db) -> tuple[list[CompanyFeatureWindow], int]:
    """Exclude periods that authoritative human review confirmed as actual risk."""
    windows = list(
        db.scalars(
            select(CompanyFeatureWindow)
            .where(CompanyFeatureWindow.data_quality != "unavailable")
            .order_by(CompanyFeatureWindow.window_start, CompanyFeatureWindow.id)
        )
    )
    event_ids = list(
        db.scalars(
            select(RiskEventLabel.risk_event_id)
            .where(RiskEventLabel.status.in_(("confirmed", "adjudicated")))
            .distinct()
        )
    )
    periods: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for event_id in event_ids:
        label = authoritative_risk_label(db, event_id)
        if label is None or not label.is_risk:
            continue
        event = db.get(RiskEvent, event_id)
        if event is None:
            continue
        end = label.event_end or event.last_seen_at or label.event_start
        periods[event.company_id].append((label.event_start, end))
    eligible = [
        window
        for window in windows
        if not any(
            start <= window.window_start <= end
            for start, end in periods.get(window.company_id, [])
        )
    ]
    return eligible, len(windows) - len(eligible)


def _vector(window: CompanyFeatureWindow, names: list[str]) -> list[float]:
    values = dict(window.feature_values or {})
    values["anomaly_score"] = window.anomaly_score or 0.0
    values["anomaly_percentile"] = window.anomaly_percentile or 0.0
    return [float(values.get(name, 0.0)) for name in names]


def _scaler(values: np.ndarray) -> dict[str, list[float]]:
    center = np.median(values, axis=0)
    q75, q25 = np.percentile(values, [75, 25], axis=0)
    scale = np.where(np.abs(q75 - q25) < 1e-9, 1.0, q75 - q25)
    return {"center": center.tolist(), "scale": scale.tolist()}


def _apply_scaler(values: np.ndarray, scaler: dict) -> np.ndarray:
    center = np.asarray(scaler["center"], dtype=float)
    scale = np.asarray(scaler["scale"], dtype=float)
    return (values - center) / np.where(np.abs(scale) < 1e-9, 1.0, scale)


def train_isolation_forest(output_root: Path) -> dict:
    """Fit a shared IF after per-company robust normalization and register a candidate."""
    import joblib
    from sklearn.ensemble import IsolationForest

    with SessionLocal() as db:
        windows, excluded_risk_windows = _if_training_windows(db)
    if len(windows) < 200:
        raise ValueError(f"유효 특징 창 {len(windows)}/200개: Isolation Forest 학습 기준 미달")
    grouped: dict[int, list[CompanyFeatureWindow]] = defaultdict(list)
    for window in windows:
        grouped[window.company_id].append(window)
    scalers: dict[str, dict] = {}
    normalized: list[np.ndarray] = []
    all_values = np.asarray([_vector(window, BASE_FEATURE_NAMES) for window in windows], dtype=float)
    scalers["global"] = _scaler(all_values)
    for company_id, company_windows in grouped.items():
        values = np.asarray([_vector(window, BASE_FEATURE_NAMES) for window in company_windows], dtype=float)
        scalers[str(company_id)] = _scaler(values)
        normalized.extend(_apply_scaler(values, scalers[str(company_id)]))
    matrix = np.asarray(normalized, dtype=float)
    model = IsolationForest(
        n_estimators=300,
        max_samples="auto",
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    ).fit(matrix)
    scores = -model.decision_function(matrix)
    version = version_stamp("iforest")
    output = output_root / f"{version}.joblib"
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "feature_names": BASE_FEATURE_NAMES,
            "company_scalers": scalers,
            "training_scores": scores.tolist(),
        },
        output,
    )
    rows_for_hash = [
        {"id": item.id, "company": item.company_id, "start": item.window_start, "features": _vector(item, BASE_FEATURE_NAMES)}
        for item in windows
    ]
    metrics = {
        "training_windows": len(windows),
        "excluded_confirmed_risk_windows": excluded_risk_windows,
        "score_p95": float(np.percentile(scores, 95)),
        "score_p99": float(np.percentile(scores, 99)),
    }
    registry = register_candidate(
        task="isolation_forest",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(rows_for_hash),
        label_schema={"target": "unsupervised_non_confirmed_risk_window"},
        metrics=metrics,
        thresholds={"p95": metrics["score_p95"], "p99": metrics["score_p99"]},
        training_counts={"windows": len(windows), "companies": len(grouped)},
    )
    return {"model_version_id": registry.id, "version": version, "metrics": metrics}


def _labeled_windows() -> tuple[list[dict], dict]:
    with SessionLocal() as db:
        label_rows = db.execute(
            select(RiskEventLabel, RiskEvent)
            .join(RiskEvent, RiskEvent.id == RiskEventLabel.risk_event_id)
            .where(RiskEventLabel.status.in_(["confirmed", "adjudicated"]))
            .order_by(RiskEventLabel.event_start, RiskEventLabel.id)
        ).all()
        records: list[dict] = []
        for label, event in label_rows:
            end = label.event_end or event.last_seen_at or label.event_start
            windows = list(
                db.scalars(
                    select(CompanyFeatureWindow).where(
                        CompanyFeatureWindow.company_id == event.company_id,
                        CompanyFeatureWindow.window_start >= label.event_start,
                        CompanyFeatureWindow.window_start <= end,
                        CompanyFeatureWindow.data_quality != "unavailable",
                    )
                )
            )
            for window in windows:
                records.append(
                    {
                        "window": window,
                        "event_id": event.id,
                        "company_id": event.company_id,
                        "time": window.window_start,
                        "target": int(label.is_risk),
                    }
                )
    event_counts = Counter((event.id, int(label.is_risk)) for label, event in label_rows)
    independent = Counter(target for (_, target) in event_counts)
    return records, {"positive_events": independent[1], "negative_events": independent[0]}


def _best_f2_threshold(targets: np.ndarray, probabilities: np.ndarray) -> float:
    from sklearn.metrics import fbeta_score

    best_threshold, best_score = 0.65, -1.0
    for threshold in np.linspace(0.2, 0.9, 71):
        score = fbeta_score(targets, probabilities >= threshold, beta=2, zero_division=0)
        if score > best_score:
            best_threshold, best_score = float(threshold), float(score)
    return best_threshold


def train_risk_detector(output_root: Path, isolation_artifact: Path | None = None) -> dict:
    """Train a pooled classifier with event-group splitting and equalized event/company weights."""
    import joblib
    from lightgbm import LGBMClassifier
    from sklearn.metrics import average_precision_score, f1_score, fbeta_score, recall_score

    records, independent = _labeled_windows()
    if independent["positive_events"] < 20 or independent["negative_events"] < 60:
        raise ValueError(
            "위험/정상 독립 사건 "
            f"{independent['positive_events']}/{independent['negative_events']}건: 최소 20/60건 필요"
        )
    splits = chronological_group_split(
        records,
        group_key=lambda row: str(row["event_id"]),
        time_key=lambda row: row["time"],
    )
    with SessionLocal() as db:
        production_if_versions = list(
            db.scalars(
                select(ModelVersion)
                .where(
                    ModelVersion.task == "isolation_forest",
                    ModelVersion.status == "production",
                )
                .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.id.desc())
            )
        )
    if len(production_if_versions) != 1:
        raise ValueError(
            "운영 Isolation Forest가 정확히 하나 등록되어 있어야 합니다."
        )
    if_version = production_if_versions[0]
    registered_path = Path(if_version.artifact_path).resolve()
    if isolation_artifact is not None and isolation_artifact.resolve() != registered_path:
        raise ValueError(
            "지정한 Isolation Forest가 현재 운영 레지스트리 아티팩트와 일치하지 않습니다."
        )
    isolation_artifact = registered_path
    if_hash = artifact_sha256(isolation_artifact)
    if if_hash is None:
        raise ValueError("운영 Isolation Forest 아티팩트를 읽을 수 없습니다.")
    try:
        if_payload = joblib.load(isolation_artifact)
    except Exception as exc:
        raise ValueError("운영 Isolation Forest 아티팩트를 불러올 수 없습니다.") from exc
    if not isolation_forest_artifact_is_valid(if_payload):
        raise ValueError("운영 Isolation Forest 아티팩트 계약이 올바르지 않습니다.")
    if_model = if_payload["model"]
    scalers = if_payload["company_scalers"]
    reference = np.asarray(if_payload.get("training_scores") or [], dtype=float)

    def row_features(row: dict) -> list[float]:
        window = row["window"]
        base = np.asarray([_vector(window, BASE_FEATURE_NAMES)], dtype=float)
        scaler = scalers.get(str(row["company_id"])) or scalers["global"]
        normalized = _apply_scaler(base, scaler)
        score = float(-if_model.decision_function(normalized)[0])
        percentile = float(np.mean(reference <= score)) if reference.size else 0.0
        values = dict(window.feature_values or {})
        values["anomaly_score"] = score
        values["anomaly_percentile"] = percentile
        return [float(values.get(name, 0.0)) for name in RISK_FEATURE_NAMES]

    train = splits["train"]
    X_train = np.asarray([row_features(row) for row in train], dtype=float)
    y_train = np.asarray([row["target"] for row in train], dtype=int)
    event_sizes = Counter(row["event_id"] for row in train)
    company_sizes = Counter(row["company_id"] for row in train)
    weights = np.asarray(
        [1.0 / event_sizes[row["event_id"]] / company_sizes[row["company_id"]] for row in train],
        dtype=float,
    )
    weights *= len(weights) / weights.sum()
    model = LGBMClassifier(
        objective="binary",
        n_estimators=250,
        learning_rate=0.03,
        num_leaves=15,
        min_child_samples=10,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(X_train, y_train, sample_weight=weights)
    validation = splits["validation"] or splits["test"]
    X_validation = np.asarray([row_features(row) for row in validation], dtype=float)
    y_validation = np.asarray([row["target"] for row in validation], dtype=int)
    validation_probabilities = model.predict_proba(X_validation)[:, 1]
    global_threshold = _best_f2_threshold(y_validation, validation_probabilities)
    per_company: dict[str, float] = {}
    for company_id in sorted({row["company_id"] for row in validation}):
        indices = [index for index, row in enumerate(validation) if row["company_id"] == company_id]
        targets = y_validation[indices]
        if len(indices) >= 10 and len(set(targets.tolist())) == 2 and int(targets.sum()) >= 3:
            per_company[str(company_id)] = _best_f2_threshold(targets, validation_probabilities[indices])

    metrics: dict[str, float] = {}
    for split_name, split_rows in splits.items():
        if not split_rows:
            continue
        targets = np.asarray([row["target"] for row in split_rows], dtype=int)
        probabilities = model.predict_proba(np.asarray([row_features(row) for row in split_rows]))[:, 1]
        predictions = probabilities >= global_threshold
        metrics[f"{split_name}_pr_auc"] = float(average_precision_score(targets, probabilities)) if len(set(targets)) > 1 else 0.0
        metrics[f"{split_name}_macro_f1"] = float(
            f1_score(targets, predictions, average="macro", zero_division=0)
        )
        metrics[f"{split_name}_f2"] = float(fbeta_score(targets, predictions, beta=2, zero_division=0))
        metrics[f"{split_name}_recall"] = float(recall_score(targets, predictions, zero_division=0))
        company_days = {
            (row["company_id"], row["time"].date()) for row in split_rows
        }
        false_positives = sum(
            int(target == 0 and prediction)
            for target, prediction in zip(targets, predictions)
        )
        metrics[f"{split_name}_daily_false_alarms_per_company"] = (
            float(false_positives / len(company_days)) if company_days else 0.0
        )
        for company_id in sorted({row["company_id"] for row in split_rows}):
            indices = [
                index for index, row in enumerate(split_rows)
                if row["company_id"] == company_id
            ]
            dates = {split_rows[index]["time"].date() for index in indices}
            company_false_positives = sum(
                int(targets[index] == 0 and predictions[index]) for index in indices
            )
            metrics[f"{split_name}_company_{company_id}_daily_false_alarms"] = (
                float(company_false_positives / len(dates)) if dates else 0.0
            )
    version = version_stamp("risk-lgbm")
    output = output_root / f"{version}.joblib"
    output.parent.mkdir(parents=True, exist_ok=True)
    dependencies = {
        REQUIRED_IF_VERSION_KEY: if_version.version,
        REQUIRED_IF_HASH_KEY: if_hash,
    }
    joblib.dump(
        {
            "model": model,
            "feature_names": RISK_FEATURE_NAMES,
            **dependencies,
        },
        output,
    )
    model_state = "production" if independent["positive_events"] >= 50 and independent["negative_events"] >= 150 else "provisional"
    rows_for_hash = [
        {"window": row["window"].id, "event": row["event_id"], "company": row["company_id"], "target": row["target"]}
        for row in records
    ]
    thresholds = {"global": global_threshold, "per_company": per_company, "model_state": model_state}
    registry = register_candidate(
        task="risk_detector",
        version=version,
        artifact_path=output,
        training_data_hash=dataset_hash(rows_for_hash),
        label_schema={"target": {"normal": 0, "risk": 1}, "unit": "current_15m_window"},
        metrics=metrics,
        thresholds=thresholds,
        training_counts={**independent, "labeled_windows": len(records)},
        dependencies=dependencies,
    )
    return {
        "model_version_id": registry.id,
        "version": version,
        "metrics": metrics,
        "thresholds": thresholds,
        "counts": independent,
        "dependencies": dependencies,
    }
