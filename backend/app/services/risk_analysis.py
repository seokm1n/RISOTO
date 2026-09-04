"""Build 15-minute company features and score them with shared promoted models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    Company,
    CompanyArticleMatch,
    CompanyDailySummary,
    CompanyFeatureWindow,
    CompanyKeyword,
    ModelVersion,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
    StoryClusterArticle,
)
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES
from app.services.collection_health import floor_window
from app.services.fine_tuned_text import predict_risk_types, predict_risk_types_batch
from app.services.story_clustering import backfill_story_clusters


SEOUL = ZoneInfo("Asia/Seoul")

RISK_TYPE_PATTERNS: dict[str, tuple[str, ...]] = {
    "product_quality": ("리콜", "불량", "결함", "품질", "하자", "오작동", "recall", "defect"),
    "safety_accident": ("사고", "화재", "부상", "사망", "안전", "폭발", "중독"),
    "security_privacy": ("해킹", "유출", "개인정보", "랜섬웨어", "보안", "침해"),
    "legal_regulatory": ("소송", "규제", "공정위", "검찰", "기소", "과징금", "법원", "위반"),
    "labor_hr": ("파업", "노조", "해고", "괴롭힘", "산재", "임금", "인사"),
    "financial_governance": ("횡령", "배임", "부도", "적자", "감사", "분식", "지배구조"),
    "supply_operations": ("공급망", "배송", "물류", "중단", "품절", "장애", "생산 차질"),
    "reputation_consumer": ("불매", "논란", "항의", "민원", "비판", "갑질", "소비자 피해"),
}
RISK_TYPE_HYPOTHESES = {
    "product_quality": "이 글은 제품 품질, 결함 또는 리콜 위험에 관한 내용이다.",
    "safety_accident": "이 글은 안전사고, 부상, 화재 또는 사망 위험에 관한 내용이다.",
    "security_privacy": "이 글은 보안 침해, 해킹 또는 개인정보 유출 위험에 관한 내용이다.",
    "legal_regulatory": "이 글은 소송, 규제, 수사 또는 법 위반 위험에 관한 내용이다.",
    "labor_hr": "이 글은 노동, 인사, 파업 또는 직장 내 문제 위험에 관한 내용이다.",
    "financial_governance": "이 글은 재무 악화, 회계 또는 지배구조 위험에 관한 내용이다.",
    "supply_operations": "이 글은 공급망, 배송, 생산 또는 서비스 운영 중단 위험에 관한 내용이다.",
    "reputation_consumer": "이 글은 기업 평판, 소비자 피해, 불매 또는 논란 위험에 관한 내용이다.",
}

BASE_FEATURE_NAMES = [
    "log_article_count",
    "log_story_count",
    "log_amplification_count",
    "log_publisher_count",
    "amplification_ratio",
    "source_diversity",
    "publisher_diversity",
    "publisher_concentration",
    "positive_probability",
    "neutral_probability",
    "negative_probability",
    "negative_probability_p90",
    "risk_keyword_ratio",
    "risk_keyword_story_ratio",
    "article_count_delta",
    "story_count_delta",
    "negative_probability_delta",
    "article_count_robust_z",
    "story_count_robust_z",
    "negative_probability_robust_z",
    "collection_completeness",
    "partial_source_flag",
    "no_article_flag",
    *[f"risk_type_{risk_type}" for risk_type in RISK_TYPE_PATTERNS],
]
RISK_DETECTOR_FEATURE_NAMES = [
    *BASE_FEATURE_NAMES,
    "anomaly_score",
    "anomaly_percentile",
]
REQUIRED_IF_VERSION_KEY = "required_if_version"
REQUIRED_IF_HASH_KEY = "required_if_hash"


def _article_probabilities(article: NewsArticle) -> tuple[float, float, float]:
    """Return stored probabilities, with a deterministic legacy-label fallback."""
    if all(
        value is not None
        for value in (
            article.positive_probability,
            article.neutral_probability,
            article.negative_probability,
        )
    ):
        return (
            float(article.positive_probability),
            float(article.neutral_probability),
            float(article.negative_probability),
        )
    label = (article.sentiment_label or "").casefold()
    if label in {"positive", "긍정"}:
        return 1.0, 0.0, 0.0
    if label in {"negative", "부정"}:
        return 0.0, 0.0, 1.0
    if label in {"neutral", "중립"}:
        return 0.0, 1.0, 0.0
    return 0.0, 0.0, 0.0


def classify_risk_types(texts: list[str]) -> dict[str, float]:
    """Bootstrap the fixed multi-label taxonomy with transparent keyword evidence."""
    if not texts:
        return {risk_type: 0.0 for risk_type in RISK_TYPE_PATTERNS}
    scores: dict[str, float] = {}
    lowered = [text.casefold() for text in texts]
    for risk_type, patterns in RISK_TYPE_PATTERNS.items():
        matched_articles = sum(any(pattern.casefold() in text for pattern in patterns) for text in lowered)
        scores[risk_type] = round(matched_articles / len(texts), 6)
    return scores


def enrich_risk_types_with_nli(
    texts: list[str],
    keyword_scores: dict[str, float],
    settings: Settings,
) -> dict[str, float]:
    """Blend transparent keywords with KLUE NLI, falling back without blocking analysis."""
    if not texts or not settings.risk_type_nli_enabled:
        return keyword_scores
    try:
        from app.services.klue_nli import get_klue_nli_classifier

        risk_types = list(RISK_TYPE_HYPOTHESES)
        hypotheses = [
            [*[RISK_TYPE_HYPOTHESES[item] for item in risk_types], "이 글은 기업 위험 사건과 관련이 없다."]
            for _ in texts[:24]
        ]
        classifier = get_klue_nli_classifier(
            settings.article_filter_classifier_model,
            settings.article_filter_allow_model_download,
        )
        rows = classifier.score_hypotheses(texts[:24], hypotheses, batch_size=16, max_length=256)
        nli_scores = {
            risk_type: float(np.mean([row[index] for row in rows]))
            for index, risk_type in enumerate(risk_types)
        }
        return {
            risk_type: round(max(float(keyword_scores.get(risk_type, 0.0)), 0.6 * nli_scores[risk_type]), 6)
            for risk_type in risk_types
        }
    except Exception:
        return keyword_scores


def resolve_risk_type_scores(
    texts: list[str],
    *,
    risk_keyword_count: int,
    negative_probability: float | None,
    settings: Settings,
    use_type_nli: bool = True,
) -> dict[str, float]:
    """Prefer the promoted reviewed-label model, then use keyword and NLI bootstrap."""
    promoted = predict_risk_types(texts)
    if promoted is not None:
        return {
            risk_type: float(promoted["scores"].get(risk_type, 0.0))
            for risk_type in RISK_TYPE_PATTERNS
        }
    scores = classify_risk_types(texts)
    if use_type_nli and (risk_keyword_count or (negative_probability or 0.0) >= 0.55):
        scores = enrich_risk_types_with_nli(texts, scores, settings)
    return scores


def resolve_article_risk_type_scores_batch(
    texts: list[str],
    *,
    risk_keyword_hits: list[bool],
    negative_probabilities: list[float],
    settings: Settings,
) -> list[dict[str, float]]:
    """Resolve per-article type scores without repeating model setup for every row."""
    if not (len(texts) == len(risk_keyword_hits) == len(negative_probabilities)):
        raise ValueError("기사 위험 유형 배치 입력 길이가 다릅니다.")
    if not texts:
        return []

    promoted = predict_risk_types_batch(texts)
    if promoted is not None:
        _version, rows = promoted
        return [
            {risk_type: float(row.get(risk_type, 0.0)) for risk_type in RISK_TYPE_PATTERNS}
            for row in rows
        ]

    scores = [classify_risk_types([value]) for value in texts]
    if not settings.risk_type_nli_enabled:
        return scores
    candidate_indexes = [
        index
        for index, (keyword_hit, negative) in enumerate(
            zip(risk_keyword_hits, negative_probabilities)
        )
        if keyword_hit or negative >= 0.55
    ]
    if not candidate_indexes:
        return scores
    try:
        from app.services.klue_nli import get_klue_nli_classifier

        risk_types = list(RISK_TYPE_HYPOTHESES)
        hypotheses = [
            [*[RISK_TYPE_HYPOTHESES[item] for item in risk_types], "이 글은 기업 위험 사건과 관련이 없다."]
            for _ in candidate_indexes
        ]
        classifier = get_klue_nli_classifier(
            settings.article_filter_classifier_model,
            settings.article_filter_allow_model_download,
        )
        rows = classifier.score_hypotheses(
            [texts[index] for index in candidate_indexes],
            hypotheses,
            batch_size=32,
            max_length=256,
        )
        for article_index, row in zip(candidate_indexes, rows):
            scores[article_index] = {
                risk_type: round(
                    max(float(scores[article_index].get(risk_type, 0.0)), 0.6 * float(row[type_index])),
                    6,
                )
                for type_index, risk_type in enumerate(risk_types)
            }
    except Exception:
        return scores
    return scores


def _safe_mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _window_rows(
    db: Session,
    company_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[NewsArticle, CompanyArticleMatch, int | None]]:
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    return list(
        db.execute(
            select(NewsArticle, CompanyArticleMatch, StoryClusterArticle.story_cluster_id)
            .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
            .outerjoin(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
            .where(
                CompanyArticleMatch.company_id == company_id,
                article_time >= window_start,
                article_time < window_end,
            )
            .order_by(article_time, NewsArticle.id)
        ).all()
    )


def _numeric_features(
    *,
    article_count: int,
    story_count: int,
    amplification_count: int,
    publisher_count: int,
    positive_probability: float | None,
    neutral_probability: float | None,
    negative_probability: float | None,
    negative_probability_p90: float | None,
    risk_keyword_ratio: float,
    risk_keyword_story_ratio: float,
    source_diversity: float,
    publisher_concentration: float,
    collection_completeness: float,
    previous: CompanyFeatureWindow | None,
    data_quality: str,
) -> dict[str, float]:
    """Construct only company-independent values; company ID is deliberately absent."""
    return {
        "log_article_count": math.log1p(article_count),
        "log_story_count": math.log1p(story_count),
        "log_amplification_count": math.log1p(amplification_count),
        "log_publisher_count": math.log1p(publisher_count),
        "amplification_ratio": amplification_count / max(article_count, 1),
        "source_diversity": source_diversity,
        "publisher_diversity": publisher_count / max(article_count, 1),
        "publisher_concentration": publisher_concentration,
        "positive_probability": positive_probability or 0.0,
        "neutral_probability": neutral_probability or 0.0,
        "negative_probability": negative_probability or 0.0,
        "negative_probability_p90": negative_probability_p90 or 0.0,
        "risk_keyword_ratio": risk_keyword_ratio,
        "risk_keyword_story_ratio": risk_keyword_story_ratio,
        "article_count_delta": float(article_count - (previous.article_count if previous else 0)),
        "story_count_delta": float(story_count - (previous.story_count if previous else 0)),
        "negative_probability_delta": float(
            (negative_probability or 0.0)
            - ((previous.negative_probability or 0.0) if previous else 0.0)
        ),
        "collection_completeness": collection_completeness,
        "partial_source_flag": 1.0 if data_quality == "partial" else 0.0,
        "no_article_flag": 1.0 if article_count == 0 else 0.0,
    }


def _robust_z(value: float, history: list[float]) -> float:
    """Scale against one company's past with median/MAD and a stable zero-variance fallback."""
    if len(history) < 8:
        return 0.0
    values = np.asarray(history, dtype=float)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = 1.4826 * mad
    if scale < 1e-9:
        q25, q75 = np.percentile(values, [25, 75])
        scale = float((q75 - q25) / 1.349)
    if scale < 1e-9:
        return 0.0 if abs(value - median) < 1e-9 else float(np.sign(value - median) * 10.0)
    return float(np.clip((value - median) / scale, -20.0, 20.0))


def _company_robust_features(
    db: Session,
    company_id: int,
    before: datetime,
    *,
    article_count: int,
    story_count: int,
    negative_probability: float | None,
) -> dict[str, float]:
    history = list(
        db.scalars(
            select(CompanyFeatureWindow)
            .where(
                CompanyFeatureWindow.company_id == company_id,
                CompanyFeatureWindow.window_start < before,
                CompanyFeatureWindow.data_quality != "unavailable",
            )
            .order_by(CompanyFeatureWindow.window_start.desc())
            .limit(672)
        )
    )
    return {
        "article_count_robust_z": _robust_z(
            float(article_count), [float(item.article_count) for item in history]
        ),
        "story_count_robust_z": _robust_z(
            float(story_count), [float(item.story_count) for item in history]
        ),
        "negative_probability_robust_z": _robust_z(
            float(negative_probability or 0.0),
            [float(item.negative_probability or 0.0) for item in history],
        ),
    }


def _production_model(db: Session, task: str) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.task == task, ModelVersion.status == "production")
        .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.id.desc())
        .limit(1)
    )


@lru_cache(maxsize=8)
def _load_joblib(path: str, artifact_hash: str):
    """Cache an immutable artifact identity, not only its replaceable path."""
    import joblib

    return joblib.load(path)


def artifact_sha256(path: Path) -> str | None:
    """Return a file-content identity, or ``None`` for unreadable artifacts."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def import_exported_models(db: Session, settings: Settings) -> None:
    """Register the exported topical-relevance and final-risk artifacts.

    The files are outputs of this project's training code. Importing them at
    startup keeps the read-only exports folder and the model registry in sync
    without retraining any model.
    """
    if not settings.import_exported_models:
        return
    import_dir = Path(settings.model_artifact_dir)
    if not import_dir.is_dir():
        return
    if_path = import_dir / "iforest-20260826T034154Z.joblib"
    risk_path = import_dir / "risk-lgbm-20260826T034203Z.joblib"
    topical_path = import_dir / "topical-relevance-csv-20260826T043300Z"
    if_hash = artifact_sha256(if_path)
    risk_hash = artifact_sha256(risk_path)
    topical_hash = artifact_sha256(topical_path / "model.safetensors")
    existing = {
        (model.task, model.version): model
        for model in db.scalars(select(ModelVersion)).all()
    }
    now = datetime.now(timezone.utc)

    def register(
        *,
        task: str,
        version: str,
        artifact_path: Path,
        artifact_hash: str,
        label_schema: dict,
        thresholds: dict | None = None,
        dependencies: dict | None = None,
    ) -> ModelVersion:
        for current in db.scalars(
            select(ModelVersion).where(
                ModelVersion.task == task,
                ModelVersion.status == "production",
                ModelVersion.version != version,
            )
        ):
            current.status = "retired"
            current.retired_at = now
        model = existing.get((task, version))
        if model is None:
            model = ModelVersion(
                task=task,
                version=version,
                status="production",
                artifact_path=str(artifact_path),
                training_data_hash=artifact_hash,
                label_schema=label_schema,
                metrics={"source": "exports"},
                thresholds=thresholds or {},
                training_counts={},
                dependencies=dependencies or {},
                promoted_at=now,
            )
            db.add(model)
            existing[(task, version)] = model
        else:
            model.artifact_path = str(artifact_path)
            model.training_data_hash = artifact_hash
            model.status = "production"
            model.thresholds = thresholds or model.thresholds or {}
            model.dependencies = dependencies or {}
            model.promoted_at = model.promoted_at or now
            model.retired_at = None
        return model

    if topical_hash is not None and topical_path.is_dir():
        register(
            task="topical_relevance",
            version="topical-relevance-csv-20260826T043300Z",
            artifact_path=topical_path,
            artifact_hash=topical_hash,
            label_schema={"relevance": ["irrelevant", "relevant"]},
        )
    if if_hash is not None and risk_hash is not None:
        register(
            task="isolation_forest",
            version="iforest-20260826T034154Z",
            artifact_path=if_path,
            artifact_hash=if_hash,
            label_schema={"target": "unsupervised_non_confirmed_risk_window"},
        )
        register(
            task="risk_detector",
            version="lightgbm_auto_v3",
            artifact_path=risk_path,
            artifact_hash=risk_hash,
            label_schema={"target": "confirmed_risk_event"},
            thresholds={
                "global": settings.risk_default_threshold,
                "model_state": "production",
            },
            dependencies={
                REQUIRED_IF_VERSION_KEY: "iforest-20260826T034154Z",
                REQUIRED_IF_HASH_KEY: if_hash,
            },
        )
    db.flush()


def _artifact_payload(
    model: ModelVersion,
    *,
    artifact_hash: str | None = None,
) -> dict | None:
    path = Path(model.artifact_path)
    resolved_hash = artifact_hash or artifact_sha256(path)
    if resolved_hash is None:
        return None
    try:
        payload = _load_joblib(str(path.resolve()), resolved_hash)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else {"model": payload}


def isolation_forest_artifact_is_valid(payload: dict | None) -> bool:
    """Validate the serving shape shared by IF training, status and inference."""
    if not isinstance(payload, dict):
        return False
    if not callable(getattr(payload.get("model"), "decision_function", None)):
        return False
    if payload.get("feature_names") != BASE_FEATURE_NAMES:
        return False
    scalers = payload.get("company_scalers")
    if not isinstance(scalers, dict) or not isinstance(scalers.get("global"), dict):
        return False
    for scaler in scalers.values():
        if not isinstance(scaler, dict):
            return False
        try:
            center = np.asarray(scaler.get("center"), dtype=float)
            scale = np.asarray(scaler.get("scale"), dtype=float)
        except (TypeError, ValueError):
            return False
        if center.shape != (len(BASE_FEATURE_NAMES),) or scale.shape != center.shape:
            return False
        if not np.all(np.isfinite(center)) or not np.all(np.isfinite(scale)):
            return False
    return True


@dataclass(frozen=True, slots=True)
class RiskDetectorRuntime:
    """Validated production LightGBM runtime shared by scoring and status APIs."""

    version: ModelVersion | None
    payload: dict | None
    reason: str | None
    isolation_version: ModelVersion | None = None
    isolation_payload: dict | None = None
    isolation_artifact_hash: str | None = None

    @property
    def available(self) -> bool:
        return (
            self.version is not None
            and self.payload is not None
            and self.isolation_version is not None
            and self.isolation_payload is not None
            and self.isolation_artifact_hash is not None
            and self.reason is None
        )


def resolve_production_risk_detector(db: Session) -> RiskDetectorRuntime:
    """Fail closed unless one production detector satisfies the serving contract."""
    version = _production_model(db, "risk_detector")
    if version is None:
        return RiskDetectorRuntime(None, None, "production_lightgbm_not_registered")
    payload = _artifact_payload(version)
    if payload is None:
        return RiskDetectorRuntime(version, None, "artifact_unavailable")
    model = payload.get("model")
    feature_names = payload.get("feature_names")
    supports_prediction = callable(getattr(model, "predict_proba", None)) or callable(
        getattr(model, "predict", None)
    )
    if (
        not supports_prediction
        or not isinstance(feature_names, list)
        or feature_names != RISK_DETECTOR_FEATURE_NAMES
    ):
        return RiskDetectorRuntime(version, None, "artifact_contract_invalid")

    payload_if_version = payload.get(REQUIRED_IF_VERSION_KEY)
    payload_if_hash = payload.get(REQUIRED_IF_HASH_KEY)
    registry_dependencies = dict(version.dependencies or {})
    registry_if_version = registry_dependencies.get(REQUIRED_IF_VERSION_KEY)
    registry_if_hash = registry_dependencies.get(REQUIRED_IF_HASH_KEY)
    if (
        not isinstance(payload_if_version, str)
        or not payload_if_version
        or not isinstance(payload_if_hash, str)
        or len(payload_if_hash) != 64
        or payload_if_version != registry_if_version
        or payload_if_hash != registry_if_hash
    ):
        return RiskDetectorRuntime(
            version,
            None,
            "isolation_dependency_manifest_invalid",
        )

    isolation_version = _production_model(db, "isolation_forest")
    if isolation_version is None:
        return RiskDetectorRuntime(
            version,
            None,
            "production_isolation_forest_not_registered",
        )
    if isolation_version.version != payload_if_version:
        return RiskDetectorRuntime(
            version,
            None,
            "isolation_dependency_mismatch",
            isolation_version=isolation_version,
        )
    isolation_hash = artifact_sha256(Path(isolation_version.artifact_path))
    if isolation_hash is None:
        return RiskDetectorRuntime(
            version,
            None,
            "isolation_artifact_unavailable",
            isolation_version=isolation_version,
        )
    if isolation_hash != payload_if_hash:
        return RiskDetectorRuntime(
            version,
            None,
            "isolation_dependency_mismatch",
            isolation_version=isolation_version,
            isolation_artifact_hash=isolation_hash,
        )
    isolation_payload = _artifact_payload(
        isolation_version,
        artifact_hash=isolation_hash,
    )
    if not isolation_forest_artifact_is_valid(isolation_payload):
        return RiskDetectorRuntime(
            version,
            None,
            "isolation_artifact_contract_invalid",
            isolation_version=isolation_version,
            isolation_artifact_hash=isolation_hash,
        )
    return RiskDetectorRuntime(
        version,
        payload,
        None,
        isolation_version=isolation_version,
        isolation_payload=isolation_payload,
        isolation_artifact_hash=isolation_hash,
    )


def _mark_risk_detection_unavailable(window: CompanyFeatureWindow) -> None:
    """Clear stale scores whenever final LightGBM judgment cannot run."""
    window.anomaly_score = None
    window.anomaly_percentile = None
    window.risk_probability = None
    window.decision_threshold = None
    window.is_risk = False
    window.model_state = "unavailable"
    window.model_version = None
    window.scored_at = None


def _scale_features(
    feature_values: dict[str, float],
    feature_names: list[str],
    scalers: dict,
    company_id: int,
) -> np.ndarray:
    raw = np.asarray([feature_values.get(name, 0.0) for name in feature_names], dtype=float)
    scaler = scalers.get(str(company_id)) or scalers.get("global") or {}
    center = np.asarray(scaler.get("center", [0.0] * len(feature_names)), dtype=float)
    scale = np.asarray(scaler.get("scale", [1.0] * len(feature_names)), dtype=float)
    scale = np.where(np.abs(scale) < 1e-9, 1.0, scale)
    return (raw - center) / scale


def score_window(db: Session, window: CompanyFeatureWindow, settings: Settings) -> None:
    """Apply one shared IF and one shared LightGBM, with per-company calibration."""
    if window.data_quality == "unavailable":
        _mark_risk_detection_unavailable(window)
        return

    detector = resolve_production_risk_detector(db)
    if not detector.available:
        _mark_risk_detection_unavailable(window)
        return

    if_version = detector.isolation_version
    if_payload = detector.isolation_payload
    risk_version = detector.version
    risk_payload = detector.payload

    risk_features = dict(window.feature_values or {})
    assert if_payload is not None and if_version is not None
    try:
        names = list(if_payload.get("feature_names") or BASE_FEATURE_NAMES)
        scaled = _scale_features(
            risk_features,
            names,
            if_payload.get("company_scalers") or {},
            window.company_id,
        )
        model = if_payload.get("model")
        raw_score = float(-model.decision_function([scaled])[0])
        reference = np.asarray(if_payload.get("training_scores") or [], dtype=float)
        percentile = float(np.mean(reference <= raw_score)) if reference.size else 0.0
        if not np.isfinite(raw_score) or not np.isfinite(percentile):
            raise ValueError("Isolation Forest returned a non-finite score")
    except Exception:
        _mark_risk_detection_unavailable(window)
        return
    window.anomaly_score = raw_score
    window.anomaly_percentile = percentile
    risk_features["anomaly_score"] = raw_score
    risk_features["anomaly_percentile"] = percentile
    window.feature_values = risk_features

    # The validated contract above guarantees both values and exact feature order.
    assert risk_payload is not None and risk_version is not None
    names = list(risk_payload["feature_names"])
    vector = np.asarray([[risk_features.get(name, 0.0) for name in names]], dtype=float)
    model = risk_payload.get("model")
    try:
        if hasattr(model, "predict_proba"):
            probability = float(model.predict_proba(vector)[0][1])
        else:
            prediction = model.predict(vector)
            probability = float(prediction[0])
        if not np.isfinite(probability):
            raise ValueError("LightGBM returned a non-finite probability")
    except Exception:
        _mark_risk_detection_unavailable(window)
        return

    thresholds = dict(risk_version.thresholds or {})
    per_company = thresholds.get("per_company") or {}
    threshold = float(per_company.get(str(window.company_id), thresholds.get("global", settings.risk_default_threshold)))
    window.risk_probability = max(0.0, min(1.0, probability))
    window.decision_threshold = threshold
    window.is_risk = window.risk_probability >= threshold
    window.model_state = str(thresholds.get("model_state") or "production")
    window.model_version = risk_version.version
    window.scored_at = datetime.now(timezone.utc)


def _event_articles(
    db: Session,
    company_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[NewsArticle]:
    return [row[0] for row in _window_rows(db, company_id, window_start, window_end)]


def update_risk_events(
    db: Session,
    window: CompanyFeatureWindow,
    settings: Settings,
) -> tuple[int, bool] | None:
    """Open on the first risky window and close with two-window hysteresis."""
    if window.risk_probability is None:
        return None
    open_events = list(
        db.scalars(
            select(RiskEvent)
            .where(
                RiskEvent.company_id == window.company_id,
                RiskEvent.status.in_(["open", "monitoring", "acknowledged"]),
            )
            .order_by(RiskEvent.last_seen_at.desc())
        )
    )
    if not window.is_risk:
        for event in open_events:
            if (window.risk_probability or 0.0) < settings.risk_close_threshold:
                event.consecutive_below += 1
                if event.consecutive_below >= settings.risk_close_consecutive_windows:
                    event.status = "closed"
                    event.closed_at = window.window_end
            else:
                event.consecutive_below = 0
        return None

    selected_types = [
        (risk_type, score)
        for risk_type, score in (window.risk_type_scores or {}).items()
        if float(score) >= 0.20
    ]
    selected_types.sort(key=lambda item: item[1], reverse=True)
    if not selected_types:
        selected_types = [("reputation_consumer", 0.01)]
    primary_type = selected_types[0][0]
    merge_gap = timedelta(
        minutes=settings.collection_window_minutes
        * max(1, settings.risk_close_consecutive_windows)
    )
    recent = next(
        (
            event
            for event in open_events
            if event.last_seen_at >= window.window_start - merge_gap
            and event.opened_at <= window.window_end + merge_gap
        ),
        None,
    )
    severity = "critical" if (window.risk_probability or 0.0) >= 0.85 else "warning"
    should_generate_draft = recent is None
    if recent is None:
        recent = RiskEvent(
            company_id=window.company_id,
            feature_window_id=window.id,
            article_id=None,
            anomaly_score=window.anomaly_score or 0.0,
            risk_probability=window.risk_probability,
            severity=severity,
            status="open",
            primary_type=primary_type,
            summary=f"15분 구간에서 {primary_type} 위험 신호가 감지되었습니다.",
            model_version=window.model_version,
            model_state=window.model_state,
            approval_state="draft",
            opened_at=window.window_start,
            last_seen_at=window.window_end,
        )
        db.add(recent)
        db.flush()
    else:
        should_generate_draft = recent.primary_type != primary_type
        recent.status = "monitoring"
        recent.feature_window_id = window.id
        recent.anomaly_score = max(recent.anomaly_score, window.anomaly_score or 0.0)
        recent.risk_probability = max(recent.risk_probability or 0.0, window.risk_probability or 0.0)
        recent.severity = "critical" if "critical" in {recent.severity, severity} else "warning"
        recent.primary_type = primary_type
        recent.last_seen_at = window.window_end
        recent.consecutive_below = 0

    articles = _event_articles(db, window.company_id, window.window_start, window.window_end)
    for article in articles:
        if recent.article_id is None:
            recent.article_id = article.id
        link = db.get(RiskEventArticle, (recent.id, article.id))
        if link is None:
            _, _, negative = _article_probabilities(article)
            db.add(
                RiskEventArticle(
                    risk_event_id=recent.id,
                    article_id=article.id,
                    evidence_score=negative,
                )
            )
    for link in db.scalars(
        select(RiskEventType).where(RiskEventType.risk_event_id == recent.id)
    ):
        link.is_primary = False
    for index, (risk_type, probability) in enumerate(selected_types):
        link = db.get(RiskEventType, (recent.id, risk_type))
        if link is None:
            db.add(
                RiskEventType(
                    risk_event_id=recent.id,
                    risk_type=risk_type,
                    probability=float(probability),
                    is_primary=index == 0,
                    evidence={"source": "keyword_bootstrap"},
                )
            )
        else:
            link.probability = max(link.probability, float(probability))
            link.is_primary = index == 0
    db.flush()
    return recent.id, should_generate_draft


def update_daily_summary(db: Session, company_id: int, at: datetime) -> None:
    """Idempotently rebuild one Seoul-local day from the same valid-window article cohort."""
    local_day = at.astimezone(SEOUL).date()
    local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=SEOUL)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    windows = list(
        db.scalars(
            select(CompanyFeatureWindow).where(
                CompanyFeatureWindow.company_id == company_id,
                CompanyFeatureWindow.window_start >= utc_start,
                CompanyFeatureWindow.window_start < utc_end,
            )
        )
    )
    summary = db.scalar(
        select(CompanyDailySummary).where(
            CompanyDailySummary.company_id == company_id,
            CompanyDailySummary.summary_date == local_day,
        )
    )
    if summary is None:
        summary = CompanyDailySummary(company_id=company_id, summary_date=local_day)
        db.add(summary)
    valid = [item for item in windows if item.data_quality != "unavailable"]
    summary.article_count = sum(item.article_count for item in valid)
    summary.story_count = sum(item.story_count for item in valid)
    summary.amplification_count = sum(item.amplification_count for item in valid)
    summary.publisher_count = max((item.publisher_count for item in valid), default=0)
    summary.positive_probability = _safe_mean(
        [item.positive_probability for item in valid if item.positive_probability is not None]
    )
    summary.neutral_probability = _safe_mean(
        [item.neutral_probability for item in valid if item.neutral_probability is not None]
    )
    summary.negative_probability = _safe_mean(
        [item.negative_probability for item in valid if item.negative_probability is not None]
    )
    valid_intervals = [(item.window_start, item.window_end) for item in valid]
    day_rows = _window_rows(db, company_id, utc_start, utc_end) if valid_intervals else []
    eligible_articles = {
        article.id: article
        for article, _match, _story_id in day_rows
        if any(
            interval_start <= (article.published_at or article.created_at) < interval_end
            for interval_start, interval_end in valid_intervals
        )
    }
    sentiment_counts = Counter(
        (article.sentiment_label or "").casefold()
        for article in eligible_articles.values()
    )
    summary.positive_article_count = sum(sentiment_counts[label] for label in ("positive", "긍정"))
    summary.neutral_article_count = sum(sentiment_counts[label] for label in ("neutral", "중립"))
    summary.negative_article_count = sum(sentiment_counts[label] for label in ("negative", "부정"))
    summary.risk_article_count = 0
    if eligible_articles:
        summary.risk_article_count = db.scalar(
            select(func.count(func.distinct(RiskEventArticle.article_id)))
            .join(RiskEvent, RiskEvent.id == RiskEventArticle.risk_event_id)
            .where(
                RiskEvent.company_id == company_id,
                RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
                RiskEventArticle.article_id.in_(eligible_articles),
            )
        ) or 0
    summary.risk_event_count = db.scalar(
        select(func.count(RiskEvent.id)).where(
            RiskEvent.company_id == company_id,
            RiskEvent.opened_at >= utc_start,
            RiskEvent.opened_at < utc_end,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        )
    ) or 0
    summary.unavailable_window_count = sum(item.data_quality == "unavailable" for item in windows)
    summary.partial_window_count = sum(item.data_quality == "partial" for item in windows)


def update_company_readiness(db: Session, company: Company, settings: Settings) -> None:
    """Update model readiness without blocking or changing collection controls."""
    article_count = db.scalar(
        select(func.count(CompanyArticleMatch.article_id)).where(
            CompanyArticleMatch.company_id == company.id
        )
    ) or 0
    valid_windows = db.scalar(
        select(func.count(CompanyFeatureWindow.id)).where(
            CompanyFeatureWindow.company_id == company.id,
            CompanyFeatureWindow.data_quality != "unavailable",
            CompanyFeatureWindow.article_count > 0,
        )
    ) or 0
    if (
        article_count >= settings.readiness_min_articles
        and valid_windows >= settings.readiness_min_nonempty_windows
    ):
        company.analysis_status = "ready"
        company.baseline_ready_at = company.baseline_ready_at or datetime.now(timezone.utc)
    elif company.analysis_status != "error":
        company.analysis_status = "warming"


def build_feature_window(
    company_id: int,
    window_start: datetime,
    data_quality: str,
    successful_sources: list[str],
    failed_sources: list[str],
    *,
    use_type_nli: bool = True,
    allow_scoring: bool = True,
    force_scoring: bool = False,
    update_events: bool = True,
    generate_response_drafts: bool = True,
) -> CompanyFeatureWindow:
    """Upsert, score and summarize one company window without scoring outages."""
    settings = get_settings()
    window_end = window_start + timedelta(minutes=settings.collection_window_minutes)
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            raise ValueError("기업을 찾을 수 없습니다.")
        rows = _window_rows(db, company_id, window_start, window_end)
        articles = [row[0] for row in rows]
        article_count = len(articles)
        story_ids = {story_id if story_id is not None else article.id for article, _, story_id in rows}
        story_count = len(story_ids)
        amplification_count = max(0, article_count - story_count)
        sources = {article.source for article in articles}
        publisher_domains = [
            urlsplit(article.url).netloc.casefold() or article.source
            for article in articles
        ]
        publisher_count = len(set(publisher_domains))
        publisher_concentration = (
            max(Counter(publisher_domains).values()) / article_count
            if publisher_domains else 0.0
        )
        source_diversity = len(sources) / max(article_count, 1)
        probabilities = [_article_probabilities(article) for article in articles]
        positives = [item[0] for item in probabilities if sum(item) > 0]
        neutrals = [item[1] for item in probabilities if sum(item) > 0]
        negatives = [item[2] for item in probabilities if sum(item) > 0]
        positive_probability = _safe_mean(positives)
        neutral_probability = _safe_mean(neutrals)
        negative_probability = _safe_mean(negatives)
        negative_p90 = float(np.percentile(negatives, 90)) if negatives else None

        risk_keywords = list(
            db.scalars(
                select(CompanyKeyword.value).where(
                    CompanyKeyword.company_id == company_id,
                    CompanyKeyword.keyword_type == "risk",
                )
            )
        )
        texts = [" ".join(filter(None, [article.title, article.summary])) for article in articles]
        risk_matches = [
            any(keyword.casefold() in text.casefold() for keyword in risk_keywords)
            for text in texts
        ]
        risk_keyword_count = sum(risk_matches)
        risk_keyword_ratio = risk_keyword_count / max(article_count, 1)
        risk_story_ids = {
            story_id if story_id is not None else article.id
            for (article, _, story_id), matched in zip(rows, risk_matches)
            if matched
        }
        risk_keyword_story_ratio = len(risk_story_ids) / max(story_count, 1)
        previous = db.scalar(
            select(CompanyFeatureWindow)
            .where(
                CompanyFeatureWindow.company_id == company_id,
                CompanyFeatureWindow.window_start < window_start,
            )
            .order_by(CompanyFeatureWindow.window_start.desc())
            .limit(1)
        )
        features = _numeric_features(
            article_count=article_count,
            story_count=story_count,
            amplification_count=amplification_count,
            publisher_count=publisher_count,
            positive_probability=positive_probability,
            neutral_probability=neutral_probability,
            negative_probability=negative_probability,
            negative_probability_p90=negative_p90,
            risk_keyword_ratio=risk_keyword_ratio,
            risk_keyword_story_ratio=risk_keyword_story_ratio,
            source_diversity=source_diversity,
            publisher_concentration=publisher_concentration,
            collection_completeness=(
                len(successful_sources) / max(len(successful_sources) + len(failed_sources), 1)
            ),
            previous=previous,
            data_quality=data_quality,
        )
        features.update(
            _company_robust_features(
                db,
                company_id,
                window_start,
                article_count=article_count,
                story_count=story_count,
                negative_probability=negative_probability,
            )
        )
        type_scores = resolve_risk_type_scores(
            texts,
            risk_keyword_count=risk_keyword_count,
            negative_probability=negative_probability,
            settings=settings,
            use_type_nli=use_type_nli,
        )
        features.update(
            {f"risk_type_{risk_type}": float(score) for risk_type, score in type_scores.items()}
        )
        window = db.scalar(
            select(CompanyFeatureWindow).where(
                CompanyFeatureWindow.company_id == company_id,
                CompanyFeatureWindow.window_start == window_start,
            )
        )
        if window is None:
            window = CompanyFeatureWindow(
                company_id=company_id,
                window_start=window_start,
                window_end=window_end,
                data_quality=data_quality,
            )
            db.add(window)
        window.window_end = window_end
        window.data_quality = data_quality
        window.successful_sources = sorted(set(successful_sources))
        window.failed_sources = sorted(set(failed_sources))
        window.article_count = article_count
        window.story_count = story_count
        window.amplification_count = amplification_count
        window.publisher_count = publisher_count
        window.positive_probability = positive_probability
        window.neutral_probability = neutral_probability
        window.negative_probability = negative_probability
        window.negative_probability_p90 = negative_p90
        window.risk_keyword_count = risk_keyword_count
        window.risk_keyword_ratio = risk_keyword_ratio
        window.risk_type_scores = type_scores
        window.feature_values = features
        db.flush()
        draft_request = None
        if allow_scoring and (force_scoring or company.monitoring_status == "active"):
            score_window(db, window, settings)
            if update_events:
                draft_request = update_risk_events(db, window, settings)
        else:
            window.anomaly_score = None
            window.anomaly_percentile = None
            window.risk_probability = None
            window.decision_threshold = None
            window.is_risk = False
            window.model_state = "unavailable"
            window.model_version = None
            window.scored_at = None
        update_daily_summary(db, company_id, window_start)
        update_company_readiness(db, company, settings)
        db.commit()
        db.refresh(window)
        if generate_response_drafts and draft_request is not None and draft_request[1]:
            from app.services.response_engine import enqueue_response_draft

            # force=True로 부르면 "이미 만든 초안이 있는가" 검사를 통째로 건너뛴다.
            # 워커 둘이 같은 이벤트를 집으면 그대로 두 번 생성된다(실제로 event 1076에
            # 24초 간격 중복이 났다). 재생성이 필요한 경우는 탐지 유형이 바뀐 때인데,
            # 그 판단은 generate_response_draft가 저장된 detection_type과 비교해서 한다.
            enqueue_response_draft(draft_request[0], auto=True)
        return window


def backfill_historical_windows(company_id: int | None = None) -> dict[str, int]:
    """Build non-empty historical windows and story clusters without inventing outage state."""
    settings = get_settings()
    clustered = 0
    with SessionLocal() as db:
        while True:
            count = backfill_story_clusters(db, limit=500)
            db.commit()
            clustered += count
            if count < 500:
                break
        company_query = select(Company.id)
        if company_id is not None:
            company_query = company_query.where(Company.id == company_id)
        company_ids = list(db.scalars(company_query.order_by(Company.id)))

    windows: set[tuple[int, datetime]] = set()
    with SessionLocal() as db:
        article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
        query = (
            select(CompanyArticleMatch.company_id, article_time)
            .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
            .where(CompanyArticleMatch.company_id.in_(company_ids))
        )
        for row_company_id, timestamp in db.execute(query):
            if timestamp is not None:
                windows.add(
                    (
                        row_company_id,
                        floor_window(timestamp, settings.collection_window_minutes),
                    )
                )
    built = 0
    for row_company_id, window_start in sorted(windows, key=lambda item: (item[0], item[1])):
        with SessionLocal() as db:
            sources = list(
                db.scalars(
                    select(NewsArticle.source)
                    .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
                    .where(
                        CompanyArticleMatch.company_id == row_company_id,
                        func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= window_start,
                        func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
                        < window_start + timedelta(minutes=settings.collection_window_minutes),
                    )
                    .distinct()
                )
            )
        build_feature_window(
            row_company_id,
            window_start,
            "complete",
            sources,
            [],
            use_type_nli=False,
            allow_scoring=False,
        )
        built += 1
    return {"clustered_articles": clustered, "feature_windows": built}


def reanalyze_historical_windows(
    *,
    user_id: int | None = None,
    company_id: int | None = None,
) -> dict[str, int]:
    """Rebuild and score all stored windows after production models are connected.

    Existing collection-quality metadata is preserved. Risk events are created
    only for windows that had never received a final-risk score, which keeps a
    repeated operator run idempotent.
    """
    settings = get_settings()
    with SessionLocal() as db:
        detector = resolve_production_risk_detector(db)
        if not detector.available:
            raise ValueError("운영 LightGBM과 호환 Isolation Forest를 먼저 연결해야 합니다.")
        company_query = select(Company.id)
        if user_id is not None:
            company_query = company_query.where(Company.user_id == user_id)
        if company_id is not None:
            company_query = company_query.where(Company.id == company_id)
        company_ids = list(db.scalars(company_query.order_by(Company.id)))
        if not company_ids:
            return {"feature_windows": 0, "risk_scored_windows": 0}

        existing = {
            (window.company_id, window.window_start): {
                "data_quality": window.data_quality,
                "successful_sources": list(window.successful_sources or []),
                "failed_sources": list(window.failed_sources or []),
                "needs_event_update": window.risk_probability is None,
            }
            for window in db.scalars(
                select(CompanyFeatureWindow).where(
                    CompanyFeatureWindow.company_id.in_(company_ids)
                )
            )
        }
        article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
        article_rows = list(
            db.execute(
                select(CompanyArticleMatch.company_id, article_time)
                .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
                .where(CompanyArticleMatch.company_id.in_(company_ids))
            )
        )

    window_keys = set(existing)
    for row_company_id, timestamp in article_rows:
        if timestamp is not None:
            window_keys.add(
                (
                    row_company_id,
                    floor_window(timestamp, settings.collection_window_minutes),
                )
            )

    built = 0
    scored = 0
    for row_company_id, window_start in sorted(
        window_keys, key=lambda item: (item[0], item[1])
    ):
        state = existing.get((row_company_id, window_start))
        with SessionLocal() as db:
            sources = list(
                db.scalars(
                    select(NewsArticle.source)
                    .join(
                        CompanyArticleMatch,
                        CompanyArticleMatch.article_id == NewsArticle.id,
                    )
                    .where(
                        CompanyArticleMatch.company_id == row_company_id,
                        func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
                        >= window_start,
                        func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
                        < window_start
                        + timedelta(minutes=settings.collection_window_minutes),
                    )
                    .distinct()
                )
            )
        window = build_feature_window(
            row_company_id,
            window_start,
            state["data_quality"] if state else "complete",
            state["successful_sources"] if state else sources,
            state["failed_sources"] if state else [],
            use_type_nli=False,
            allow_scoring=True,
            force_scoring=True,
            update_events=(
                not settings.story_risk_engine_enabled
                and bool(state is None or state["needs_event_update"])
            ),
            generate_response_drafts=False,
        )
        built += 1
        scored += int(window.risk_probability is not None)
    return {"feature_windows": built, "risk_scored_windows": scored}
