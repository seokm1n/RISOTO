"""Daily model-operation checks and explicit candidate-training readiness gates."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import math
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    ArticleLabel,
    Company,
    CompanyFeatureWindow,
    ModelOperationCheck,
    ModelVersion,
    RiskEventLabel,
)
from app.risk_taxonomy import RISK_TYPES
from app.services.risk_ground_truth import authoritative_risk_label


SEOUL = ZoneInfo("Asia/Seoul")
CONFIRMED_STATUSES = ("confirmed", "adjudicated")
MONITORED_STATUSES = ("backfilling", "warming", "active", "error")
FILTER_RELEVANCE_LABELS = ("relevant", "incidental", "irrelevant")
FILTER_ADVERTISEMENT_LABELS = ("yes", "no")
SENTIMENT_LABELS = ("positive", "neutral", "negative")
DRIFT_FEATURES = (
    "article_count_robust_z",
    "story_count_robust_z",
    "negative_probability_robust_z",
    "risk_keyword_ratio",
    "source_diversity",
    "publisher_concentration",
    "collection_completeness",
)


def _latest_model(db: Session, task: str) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion)
        .where(ModelVersion.task == task)
        .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )


def _article_class_counts(db: Session, column, labels: tuple[str, ...]) -> dict[str, int]:
    rows = db.execute(
        select(column, func.count(ArticleLabel.id))
        .where(ArticleLabel.status.in_(CONFIRMED_STATUSES), column.in_(labels))
        .group_by(column)
    ).all()
    found = {str(label): int(count) for label, count in rows}
    return {label: found.get(label, 0) for label in labels}


def _article_label_count(db: Session, eligibility=None) -> int:
    query = select(func.count(ArticleLabel.id)).where(
        ArticleLabel.status.in_(CONFIRMED_STATUSES)
    )
    if eligibility is not None:
        query = query.where(eligibility)
    return int(db.scalar(query) or 0)


def _new_article_labels(
    db: Session,
    latest: ModelVersion | None,
    eligibility=None,
) -> int:
    query = select(func.count(ArticleLabel.id)).where(
        ArticleLabel.status.in_(CONFIRMED_STATUSES)
    )
    if eligibility is not None:
        query = query.where(eligibility)
    if latest is not None:
        query = query.where(ArticleLabel.reviewed_at > latest.created_at)
    return int(db.scalar(query) or 0)


def _new_risk_events(db: Session, latest: ModelVersion | None) -> int:
    query = select(func.count(distinct(RiskEventLabel.risk_event_id))).where(
        RiskEventLabel.status.in_(CONFIRMED_STATUSES)
    )
    if latest is not None:
        query = query.where(RiskEventLabel.reviewed_at > latest.created_at)
    return int(db.scalar(query) or 0)


def _risk_type_training_stats(
    db: Session,
    latest: ModelVersion | None,
) -> tuple[int, int, dict[str, int]]:
    """Count only authoritative positive events with reviewed types and evidence."""
    event_ids = list(
        db.scalars(
            select(RiskEventLabel.risk_event_id)
            .where(RiskEventLabel.status.in_(CONFIRMED_STATUSES))
            .distinct()
        )
    )
    counts = {risk_type: 0 for risk_type in RISK_TYPES}
    total = 0
    new_since_latest = 0
    for event_id in event_ids:
        label = authoritative_risk_label(db, event_id)
        if (
            label is None
            or not label.is_risk
            or not label.risk_types
            or not label.evidence_article_ids
        ):
            continue
        total += 1
        if latest is None or label.reviewed_at > latest.created_at:
            new_since_latest += 1
        for risk_type in set(label.risk_types) & set(RISK_TYPES):
            counts[risk_type] += 1
    return total, new_since_latest, counts


def _task_result(
    *,
    task: str,
    latest: ModelVersion | None,
    confirmed_total: int,
    new_since_latest: int,
    increment_required: int,
    class_counts: dict[str, int],
    minimums_met: bool,
    blockers: list[str],
    trainer_command: str,
) -> dict:
    increment_met = latest is None or new_since_latest >= increment_required
    if not increment_met:
        blockers.append(
            f"최근 후보 이후 새 확정 라벨 {new_since_latest}/{increment_required}건"
        )
    return {
        "task": task,
        "latest_model_version": latest.version if latest else None,
        "latest_model_status": latest.status if latest else None,
        "confirmed_total": confirmed_total,
        "new_since_latest": new_since_latest,
        "increment_required": increment_required,
        "class_counts": class_counts,
        "minimums_met": minimums_met,
        "increment_met": increment_met,
        "candidate_training_ready": not blockers,
        "blockers": blockers,
        "trainer_command": trainer_command,
    }


def build_training_readiness(db: Session, settings: Settings | None = None) -> dict:
    """Report gates only; this function never starts training or promotes a model."""
    settings = settings or get_settings()
    checked_at = datetime.now(timezone.utc)
    article_total = _article_label_count(db)
    filter_eligibility = or_(
        ArticleLabel.relevance_label.in_(FILTER_RELEVANCE_LABELS),
        ArticleLabel.advertisement_label.in_(FILTER_ADVERTISEMENT_LABELS),
    )
    sentiment_eligibility = ArticleLabel.sentiment_label.in_(SENTIMENT_LABELS)
    filter_total = _article_label_count(db, filter_eligibility)
    relevance = _article_class_counts(
        db, ArticleLabel.relevance_label, FILTER_RELEVANCE_LABELS
    )
    advertisement = _article_class_counts(
        db, ArticleLabel.advertisement_label, FILTER_ADVERTISEMENT_LABELS
    )
    sentiment = _article_class_counts(
        db, ArticleLabel.sentiment_label, SENTIMENT_LABELS
    )

    filter_latest = _latest_model(db, "article_filter")
    filter_blockers: list[str] = []
    if filter_total < 1500:
        filter_blockers.append(
            f"학습 가능한 필터 기사 라벨 최소 1,500건 필요 ({filter_total}/1500)"
        )
    filter_class_counts = {
        **{f"relevance:{key}": value for key, value in relevance.items()},
        **{f"advertisement:{key}": value for key, value in advertisement.items()},
    }
    for label, count in filter_class_counts.items():
        if count < 100:
            filter_blockers.append(f"{label} 클래스 최소 100건 필요 ({count}/100)")
    filter_result = _task_result(
        task="article_filter",
        latest=filter_latest,
        confirmed_total=filter_total,
        new_since_latest=_new_article_labels(db, filter_latest, filter_eligibility),
        increment_required=settings.retrain_min_new_article_labels,
        class_counts=filter_class_counts,
        minimums_met=not filter_blockers,
        blockers=filter_blockers,
        trainer_command="docker compose --profile training run --rm trainer filter --epochs 4",
    )

    reranker_latest = _latest_model(db, "company_relevance_reranker")
    reranker_counts = {
        "relevant": relevance["relevant"],
        "irrelevant_or_incidental": relevance["irrelevant"] + relevance["incidental"],
    }
    reranker_blockers: list[str] = []
    if sum(reranker_counts.values()) < 1500:
        reranker_blockers.append(
            "학습 가능한 관련성 라벨 최소 1,500건 필요 "
            f"({sum(reranker_counts.values())}/1500)"
        )
    for label, count in reranker_counts.items():
        if count < 300:
            reranker_blockers.append(f"{label} 클래스 최소 300건 필요 ({count}/300)")
    reranker_result = _task_result(
        task="company_relevance_reranker",
        latest=reranker_latest,
        confirmed_total=sum(reranker_counts.values()),
        new_since_latest=_new_article_labels(
            db,
            reranker_latest,
            ArticleLabel.relevance_label.in_(FILTER_RELEVANCE_LABELS),
        ),
        increment_required=settings.retrain_min_new_article_labels,
        class_counts=reranker_counts,
        minimums_met=not reranker_blockers,
        blockers=reranker_blockers,
        trainer_command=(
            "docker compose --profile training run --rm trainer "
            "company-reranker --epochs 2 --batch-size 2 --human-weight 1"
        ),
    )

    sentiment_latest = _latest_model(db, "sentiment")
    sentiment_total = sum(sentiment.values())
    sentiment_blockers: list[str] = []
    if sentiment_total < 1500:
        sentiment_blockers.append(
            f"학습 가능한 감성 라벨 최소 1,500건 필요 ({sentiment_total}/1500)"
        )
    for label, count in sentiment.items():
        if count < 100:
            sentiment_blockers.append(f"{label} 클래스 최소 100건 필요 ({count}/100)")
    sentiment_result = _task_result(
        task="sentiment",
        latest=sentiment_latest,
        confirmed_total=sentiment_total,
        new_since_latest=_new_article_labels(
            db, sentiment_latest, sentiment_eligibility
        ),
        increment_required=settings.retrain_min_new_article_labels,
        class_counts=sentiment,
        minimums_met=not sentiment_blockers,
        blockers=sentiment_blockers,
        trainer_command="docker compose --profile training run --rm trainer sentiment --epochs 4",
    )

    risk_type_latest = _latest_model(db, "risk_type_classifier")
    risk_type_total, new_risk_types, risk_type_counts = _risk_type_training_stats(
        db, risk_type_latest
    )
    risk_type_blockers: list[str] = []
    if risk_type_total < 200:
        risk_type_blockers.append(
            f"근거가 확정된 위험 사건 최소 200건 필요 ({risk_type_total}/200)"
        )
    for risk_type, count in risk_type_counts.items():
        if count < 20:
            risk_type_blockers.append(
                f"{risk_type} 유형 최소 20건 필요 ({count}/20)"
            )
    risk_type_result = _task_result(
        task="risk_type_classifier",
        latest=risk_type_latest,
        confirmed_total=risk_type_total,
        new_since_latest=new_risk_types,
        increment_required=settings.retrain_min_new_risk_event_labels,
        class_counts=risk_type_counts,
        minimums_met=not risk_type_blockers,
        blockers=risk_type_blockers,
        trainer_command="docker compose --profile training run --rm trainer risk-types --epochs 4",
    )

    risk_latest = _latest_model(db, "risk_detector")
    risk_counts = {
        "risk": int(
            db.scalar(
                select(func.count(distinct(RiskEventLabel.risk_event_id))).where(
                    RiskEventLabel.status.in_(CONFIRMED_STATUSES),
                    RiskEventLabel.is_risk.is_(True),
                )
            )
            or 0
        ),
        "normal": int(
            db.scalar(
                select(func.count(distinct(RiskEventLabel.risk_event_id))).where(
                    RiskEventLabel.status.in_(CONFIRMED_STATUSES),
                    RiskEventLabel.is_risk.is_(False),
                )
            )
            or 0
        ),
    }
    risk_blockers: list[str] = []
    if risk_counts["risk"] < 20:
        risk_blockers.append(
            f"위험 독립 사건 최소 20건 필요 ({risk_counts['risk']}/20)"
        )
    if risk_counts["normal"] < 60:
        risk_blockers.append(
            f"정상 독립 사건 최소 60건 필요 ({risk_counts['normal']}/60)"
        )
    risk_result = _task_result(
        task="risk_detector",
        latest=risk_latest,
        confirmed_total=sum(risk_counts.values()),
        new_since_latest=_new_risk_events(db, risk_latest),
        increment_required=settings.retrain_min_new_risk_event_labels,
        class_counts=risk_counts,
        minimums_met=not risk_blockers,
        blockers=risk_blockers,
        trainer_command="docker compose --profile training run --rm trainer risk",
    )

    return {
        "checked_at": checked_at,
        "article_labels_total": article_total,
        "risk_event_labels_total": sum(risk_counts.values()),
        "tasks": [
            filter_result,
            reranker_result,
            sentiment_result,
            risk_type_result,
            risk_result,
        ],
    }


def _numeric_feature_values(rows: list[CompanyFeatureWindow], feature: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = (row.feature_values or {}).get(feature)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def robust_distribution_shift(recent: list[float], baseline: list[float]) -> dict | None:
    """Return a robust median shift, using IQR when baseline MAD is zero."""
    if len(recent) < 8 or len(baseline) < 40:
        return None
    recent_median = float(median(recent))
    baseline_median = float(median(baseline))
    deviations = [abs(value - baseline_median) for value in baseline]
    scale = 1.4826 * float(median(deviations))
    if scale < 1e-9:
        ordered = sorted(baseline)
        q25 = ordered[int((len(ordered) - 1) * 0.25)]
        q75 = ordered[int((len(ordered) - 1) * 0.75)]
        scale = float(q75 - q25)
    if scale < 1e-9:
        scale = 1.0
    return {
        "recent_median": recent_median,
        "baseline_median": baseline_median,
        "robust_z": abs(recent_median - baseline_median) / scale,
        "recent_count": len(recent),
        "baseline_count": len(baseline),
    }


def build_daily_model_report(
    db: Session,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict:
    """Compute one read-only report for collection quality, labels and feature drift."""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    recent_start = now - timedelta(hours=settings.model_drift_recent_hours)
    baseline_start = recent_start - timedelta(days=settings.model_drift_baseline_days)
    monitored_company_ids = list(
        db.scalars(
            select(Company.id).where(Company.monitoring_status.in_(MONITORED_STATUSES))
        )
    )
    if monitored_company_ids:
        recent_rows = list(
            db.scalars(
                select(CompanyFeatureWindow).where(
                    CompanyFeatureWindow.company_id.in_(monitored_company_ids),
                    CompanyFeatureWindow.window_start >= recent_start,
                    CompanyFeatureWindow.window_start < now,
                )
            )
        )
        baseline_rows = list(
            db.scalars(
                select(CompanyFeatureWindow).where(
                    CompanyFeatureWindow.company_id.in_(monitored_company_ids),
                    CompanyFeatureWindow.window_start >= baseline_start,
                    CompanyFeatureWindow.window_start < recent_start,
                    CompanyFeatureWindow.data_quality != "unavailable",
                )
            )
        )
    else:
        recent_rows = []
        baseline_rows = []
    quality = Counter(row.data_quality for row in recent_rows)
    quality_counts = {
        name: int(quality.get(name, 0))
        for name in ("complete", "partial", "unavailable")
    }
    monitored_companies = len(monitored_company_ids)
    windows_per_company = max(
        1,
        int(settings.model_drift_recent_hours * 60 / settings.collection_window_minutes),
    )
    expected_windows = monitored_companies * windows_per_company
    missing_windows = max(expected_windows - len(recent_rows), 0)
    usable_windows = quality_counts["complete"] + quality_counts["partial"]
    collection_coverage = (
        min(usable_windows / expected_windows, 1.0)
        if expected_windows
        else None
    )

    drift_checks: list[dict] = []
    drift_flags: list[dict] = []
    usable_recent_rows = [
        row for row in recent_rows if row.data_quality != "unavailable"
    ]
    for company_id in monitored_company_ids:
        company_recent = [
            row for row in usable_recent_rows if row.company_id == company_id
        ]
        company_baseline = [
            row for row in baseline_rows if row.company_id == company_id
        ]
        for feature in DRIFT_FEATURES:
            result = robust_distribution_shift(
                _numeric_feature_values(company_recent, feature),
                _numeric_feature_values(company_baseline, feature),
            )
            if result is None:
                continue
            item = {"company_id": company_id, "feature": feature, **result}
            drift_checks.append(item)
            if result["robust_z"] >= settings.model_drift_robust_z_threshold:
                drift_flags.append(item)
    if not drift_checks:
        drift_status = "insufficient_data"
    elif drift_flags:
        drift_status = "warning"
    else:
        drift_status = "stable"

    article_distributions = {
        "relevance": _article_class_counts(
            db,
            ArticleLabel.relevance_label,
            ("relevant", "incidental", "irrelevant", "uncertain"),
        ),
        "advertisement": _article_class_counts(
            db, ArticleLabel.advertisement_label, ("yes", "no", "uncertain")
        ),
        "sentiment": _article_class_counts(
            db,
            ArticleLabel.sentiment_label,
            ("positive", "neutral", "negative", "mixed", "uncertain", "not_applicable"),
        ),
    }
    risk_distribution = {
        "risk": int(
            db.scalar(
                select(func.count(distinct(RiskEventLabel.risk_event_id))).where(
                    RiskEventLabel.status.in_(CONFIRMED_STATUSES),
                    RiskEventLabel.is_risk.is_(True),
                )
            )
            or 0
        ),
        "normal": int(
            db.scalar(
                select(func.count(distinct(RiskEventLabel.risk_event_id))).where(
                    RiskEventLabel.status.in_(CONFIRMED_STATUSES),
                    RiskEventLabel.is_risk.is_(False),
                )
            )
            or 0
        ),
    }
    quality_warning = bool(
        quality_counts["partial"]
        or quality_counts["unavailable"]
        or (expected_windows and collection_coverage is not None and collection_coverage < 0.95)
    )
    if quality_warning or drift_status == "warning":
        status = "warning"
    elif drift_status == "insufficient_data":
        status = "insufficient_data"
    else:
        status = "stable"
    return {
        "status": status,
        "period_start": recent_start.isoformat(),
        "period_end": now.isoformat(),
        "baseline_start": baseline_start.isoformat(),
        "recent_window_count": len(recent_rows),
        "baseline_window_count": len(baseline_rows),
        "monitored_company_count": monitored_companies,
        "expected_window_count": expected_windows,
        "missing_window_count": missing_windows,
        "data_quality_counts": quality_counts,
        "collection_coverage": collection_coverage,
        "article_label_distribution": article_distributions,
        "risk_label_distribution": risk_distribution,
        "drift_status": drift_status,
        "drift_threshold": settings.model_drift_robust_z_threshold,
        "drift_checks": drift_checks,
        "drift_flags": drift_flags,
    }


def ensure_daily_model_check(
    db: Session,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> ModelOperationCheck:
    """Persist at most one Seoul-calendar-day report unless explicitly forced."""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    check_date = now.astimezone(SEOUL).date()
    # Multiple API workers may reach the first check of a Seoul day together.
    # Serialize that short critical section in PostgreSQL so the unique date
    # constraint is a final guard rather than a source of avoidable 500s.
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 741_520_260_820})
    check = db.scalar(
        select(ModelOperationCheck).where(ModelOperationCheck.check_date == check_date)
    )
    if check is not None and not force:
        db.commit()
        return check
    report = build_daily_model_report(db, now=now, settings=settings)
    if check is None:
        check = ModelOperationCheck(
            check_date=check_date,
            checked_at=now,
            status=report["status"],
            report=report,
        )
        db.add(check)
    else:
        check.checked_at = now
        check.status = report["status"]
        check.report = report
    db.commit()
    db.refresh(check)
    return check
