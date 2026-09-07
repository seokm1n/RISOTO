"""Verified, cached runtime for the frozen first-24-hour story model contract.

This model estimates risk from the first eight accepted articles. It cannot
represent subsequent escalation after that window without a newly trained
contract. AI training labels remain provisional even when serving predictions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from app.services.story_model import (
    MAX_ARTICLES, NUMERIC_FEATURES, SCHEMA_VERSION, SUMMARY_LIMIT, transform_stories,
)


def snapshot_digest(snapshot: dict) -> str:
    """Use the same canonical JSON/hash convention as the training exporter."""
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _utc(value: datetime | str) -> datetime:
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(value, datetime):
        raise ValueError("Article availability and snapshot cutoff require timestamps")
    # SQLite returns naive UTC timestamps; PostgreSQL normally returns aware ones.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_story_snapshot(
    company_id: int, company_name: str, aliases: Iterable[str], articles: Iterable[Any],
    *, story_id: int, as_of: datetime | None = None,
) -> dict:
    """Freeze ORM articles using the exact feature selection used in training.

    The caller supplies only articles accepted for this company and story. Never
    substitute publication time for created_at, which is the availability time.
    """
    captured = _utc(as_of or datetime.now(timezone.utc))
    items = sorted(
        (a for a in articles if _utc(a.created_at) <= captured),
        key=lambda a: (_utc(a.created_at), a.id),
    )
    if not items:
        raise ValueError("A story needs at least one article available at the cutoff")
    start = _utc(items[0].created_at)
    cutoff = min(start + timedelta(hours=24), captured)
    evidence = []
    for item in items:
        if _utc(item.created_at) > cutoff or len(evidence) == MAX_ARTICLES:
            break
        analyzed = getattr(item, "analyzed_at", None)
        original_url = getattr(item, "original_url", None)
        published = getattr(item, "published_at", None)
        evidence.append(dict(
            id=item.id, title=item.title, summary=(getattr(item, "summary", None) or "")[:SUMMARY_LIMIT],
            url=original_url if original_url is not None else getattr(item, "url", None),
            available_at=_utc(item.created_at).isoformat(),
            published_at=_utc(published).isoformat() if published is not None else None,
            negative_probability=getattr(item, "negative_probability", None)
            if analyzed is not None and _utc(analyzed) <= cutoff else None,
        ))
    if len({a["id"] for a in evidence}) != len(evidence):
        raise ValueError("A story needs unique evidence article IDs")
    snapshot = dict(
        key=f"{company_id}:{story_id}", company_id=company_id, story_id=story_id,
        company_name=company_name, aliases=list(aliases or []), as_of=cutoff.isoformat(),
        start_at=start.isoformat(), articles=evidence,
    )
    snapshot["snapshot_hash"] = snapshot_digest(snapshot)
    return snapshot


def _probabilities(bundle: dict, matrix: np.ndarray) -> np.ndarray:
    probabilities = np.asarray(bundle["lightgbm"].predict_proba(matrix), dtype=float)
    if (probabilities.shape != (len(matrix), 2) or not np.isfinite(probabilities).all()
            or np.any(probabilities < 0) or np.any(probabilities > 1)
            or not np.allclose(probabilities.sum(axis=1), 1, atol=1e-6)):
        raise ValueError("LightGBM returned invalid binary probabilities")
    return probabilities[:, 1]


@dataclass(frozen=True)
class StoryRiskRuntime:
    version: str | None = None
    model_state: str = "provisional"
    reason: str | None = None
    message: str = (
        "스토리 Isolation Forest + LightGBM을 적용 중입니다. 최초 24시간의 기사 최대 8건 기준이며, "
        "AI 라벨로 학습해 사람 검수 기반 성능 검증은 완료되지 않았습니다."
    )
    threshold: float | None = None
    artifact_sha256: str | None = None
    _bundle: dict | None = field(default=None, repr=False, compare=False)

    @property
    def available(self) -> bool:
        return self._bundle is not None and self.reason is None

    def _unavailable(self, snapshot: dict, reason: str | None = None, message: str | None = None) -> dict:
        return dict(
            available=False, company_id=snapshot.get("company_id"), story_id=snapshot.get("story_id"),
            score=None, risk_probability=None, is_risk=None, anomaly_score=None, anomaly_percentile=None,
            threshold=self.threshold, model_version=self.version, model_state=self.model_state,
            snapshot_hash=snapshot_digest(snapshot), artifact_sha256=self.artifact_sha256,
            reason=reason or self.reason, message=message or self.message,
        )

    def predict(self, snapshots: list[dict]) -> list[dict]:
        if not snapshots:
            return []
        if not self.available:
            return [self._unavailable(snapshot) for snapshot in snapshots]
        try:
            for snapshot in snapshots:
                if snapshot.get("snapshot_hash") not in (None, snapshot_digest(snapshot)):
                    raise ValueError("Snapshot hash does not match its evidence")
            matrix = transform_stories(self._bundle, snapshots)
            if not np.isfinite(matrix).all():
                raise ValueError("Non-finite story model input")
            probabilities = _probabilities(self._bundle, matrix)
        except Exception as exc:
            return [self._unavailable(s, "prediction_failed", str(exc)) for s in snapshots]
        return [dict(
            available=True, company_id=s["company_id"], story_id=s["story_id"], score=float(p),
            risk_probability=float(p), is_risk=bool(p >= self.threshold), anomaly_score=float(x[-2]),
            anomaly_percentile=float(x[-1]), threshold=self.threshold, model_version=self.version,
            model_state=self.model_state, snapshot_hash=snapshot_digest(s), artifact_sha256=self.artifact_sha256,
            label_source="ai_generated_unreviewed", reason=None, message=self.message,
        ) for s, p, x in zip(snapshots, probabilities, matrix)]


def _validate_bundle(bundle: dict) -> None:
    from sklearn.utils.validation import check_is_fitted

    if not isinstance(bundle, dict):
        raise ValueError("Story artifact must contain a model bundle")
    if bundle.get("schema_version") != SCHEMA_VERSION or bundle.get("numeric_features") != NUMERIC_FEATURES:
        raise ValueError("Incompatible story model feature contract")
    if not isinstance(bundle.get("version"), str) or not bundle["version"].strip():
        raise ValueError("Story model version is missing")
    threshold = float(bundle["threshold"])
    if not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid story model decision threshold")
    for key in ("tfidf", "svd", "scaler", "isolation_forest", "lightgbm"):
        check_is_fitted(bundle[key])
    if list(bundle["lightgbm"].classes_) != [0, 1]:
        raise ValueError("Story model must predict normal=0 and risk=1")
    reference = np.asarray(bundle["if_reference_scores"], dtype=float)
    if (reference.ndim != 1 or not len(reference) or not np.isfinite(reference).all()
            or np.any(np.diff(reference) < 0)):
        raise ValueError("Isolation Forest reference scores must be finite and sorted")
    for key in ("scaler", "isolation_forest"):
        if int(bundle[key].n_features_in_) != len(NUMERIC_FEATURES):
            raise ValueError(f"{key} does not match the story numeric feature contract")
    expected_width = len(NUMERIC_FEATURES) + int(bundle["svd"].n_components) + 2
    if int(bundle["lightgbm"].n_features_in_) != expected_width:
        raise ValueError("LightGBM does not include the expected text and Isolation Forest features")
    probe = dict(company_id=0, story_id=0, company_name="예시기업", aliases=[],
                 as_of="2000-01-02T00:00:00+00:00", articles=[dict(
                     id=0, title="예시기업 제품 출시", summary="", url="https://example.com/news",
                     available_at="2000-01-01T00:00:00+00:00", negative_probability=None)])
    matrix = transform_stories(bundle, [probe])
    if matrix.shape != (1, expected_width) or not np.isfinite(matrix).all():
        raise ValueError("Story model preprocessing produced an invalid feature matrix")
    _probabilities(bundle, matrix)


@lru_cache(maxsize=4)
def _load_runtime(path: str, mtime_ns: int, size: int, expected_sha256: str) -> StoryRiskRuntime:
    # The stat tuple invalidates cached loads when an artifact changes. Hash the
    # same bytes that joblib deserializes so a concurrent replacement cannot
    # evade verification. SHA must be configured outside the artifact itself.
    try:
        payload = Path(path).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected_sha256:
            return StoryRiskRuntime(reason="artifact_hash_mismatch", message="Story model SHA-256 verification failed",
                                    artifact_sha256=digest)
        import joblib
        bundle = joblib.load(io.BytesIO(payload))
        _validate_bundle(bundle)
        return StoryRiskRuntime(version=bundle["version"], threshold=float(bundle["threshold"]),
                                artifact_sha256=digest, _bundle=bundle)
    except Exception as exc:
        return StoryRiskRuntime(reason="artifact_invalid", message=f"Story model cannot be loaded: {exc}")


def resolve_story_risk_runtime(settings=None) -> StoryRiskRuntime:
    if settings is None:
        from app.config import get_settings
        settings = get_settings()
    if not getattr(settings, "story_risk_model_enabled", False):
        return StoryRiskRuntime(reason="model_disabled", message="Story risk model is disabled")
    configured_path = getattr(settings, "story_risk_model_path", "")
    digest = str(getattr(settings, "story_risk_model_sha256", "") or "").strip().lower()
    if not configured_path or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return StoryRiskRuntime(reason="model_not_configured", message="Story model path and trusted SHA-256 are required")
    try:
        path = Path(configured_path).expanduser().resolve(strict=True)
        stat = path.stat()
        if not path.is_file():
            raise ValueError("Configured story model path is not a file")
    except (OSError, ValueError) as exc:
        return StoryRiskRuntime(reason="artifact_missing", message=f"Story model is unavailable: {exc}")
    return _load_runtime(str(path), stat.st_mtime_ns, stat.st_size, digest)


def score_story_snapshots(snapshots: list[dict], settings=None) -> list[dict]:
    return resolve_story_risk_runtime(settings).predict(snapshots)


def score_story_snapshot(snapshot: dict, settings=None) -> dict:
    return score_story_snapshots([snapshot], settings)[0]
