"""Collection incident, health, feature-window and daily-summary APIs."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.config import get_settings
from app.database import get_db
from app.models import (
    ArticleRiskAssessment,
    CollectionAttempt,
    CollectionIncident,
    Company,
    CompanyArticleMatch,
    CompanyDailySummary,
    CompanyFeatureWindow,
    NewsArticle,
    RiskEvent,
)
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES
from app.schemas import (
    CollectionHealthRead,
    CollectionIncidentPage,
    CollectionIncidentRead,
    CollectionSourceHealthRead,
    DailySummaryRead,
    FeatureWindowRead,
)
from app.services.period_aggregation import seoul_day_bucket, seoul_period_start


router = APIRouter(tags=["operations"])
SEOUL = ZoneInfo("Asia/Seoul")


def _user_company(db: Session, company_id: int, user_id: int) -> Company:
    company = db.scalar(
        select(Company).where(
            Company.id == company_id,
            Company.user_id == user_id,
        )
    )
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    return company


@router.get("/collection-incidents", response_model=CollectionIncidentPage)
def list_collection_incidents(
    status: str | None = Query(default=None, pattern="^(open|retrying|recovered|acknowledged)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionIncidentPage:
    query = select(CollectionIncident).where(
        CollectionIncident.user_id == auth.user_id
    )
    if status:
        query = query.where(CollectionIncident.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        db.scalars(
            query.order_by(CollectionIncident.detected_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return CollectionIncidentPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/collection-incidents/{incident_id}/acknowledge", response_model=CollectionIncidentRead)
def acknowledge_collection_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionIncident:
    incident = db.scalar(
        select(CollectionIncident).where(
            CollectionIncident.id == incident_id,
            CollectionIncident.user_id == auth.user_id,
        )
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="수집 장애를 찾을 수 없습니다.")
    if incident.status != "recovered" and incident.next_retry_at is None:
        incident.status = "acknowledged"
    incident.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/collection-health", response_model=CollectionHealthRead)
def collection_health(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionHealthRead:
    # `pipeline` is a synthetic orchestration-failure source, not a collector.
    # Its historical attempts remain auditable through incidents, but must not
    # permanently poison the per-collector health list after recovery.
    sources = list(
        db.scalars(
            select(CollectionAttempt.source)
            .where(
                CollectionAttempt.user_id == auth.user_id,
                CollectionAttempt.source != "pipeline",
            )
            .distinct()
            .order_by(CollectionAttempt.source)
        )
    )
    source_items: list[CollectionSourceHealthRead] = []
    for source in sources:
        attempts = list(
            db.scalars(
                select(CollectionAttempt)
                .where(
                    CollectionAttempt.user_id == auth.user_id,
                    CollectionAttempt.source == source,
                    CollectionAttempt.attempt_number == 0,
                )
                .order_by(CollectionAttempt.scheduled_for.desc(), CollectionAttempt.id.desc())
                .limit(200)
            )
        )
        by_window: dict[datetime, list[CollectionAttempt]] = {}
        for attempt in attempts:
            by_window.setdefault(attempt.scheduled_for, []).append(attempt)
        window_groups = [by_window[key] for key in sorted(by_window, reverse=True)]
        consecutive = 0
        for group in window_groups:
            if any(attempt.status == "succeeded" for attempt in group):
                break
            consecutive += 1
        latest = attempts[0] if attempts else None
        latest_success = next((item for item in attempts if item.status == "succeeded"), None)
        latest_group = window_groups[0] if window_groups else []
        latest_failed = any(item.status == "failed" for item in latest_group)
        latest_succeeded = any(item.status == "succeeded" for item in latest_group)
        source_items.append(
            CollectionSourceHealthRead(
                source=source,
                status=(
                    "unknown" if latest is None else
                    "partial" if latest_failed and latest_succeeded else
                    "healthy" if latest_succeeded else
                    "partial" if consecutive == 1 else "down"
                ),
                last_attempt_at=latest.completed_at if latest else None,
                last_success_at=latest_success.completed_at if latest_success else None,
                consecutive_failures=consecutive,
            )
        )
    open_incident_rows = list(
        db.scalars(
            select(CollectionIncident).where(
                CollectionIncident.user_id == auth.user_id,
                CollectionIncident.status.in_(["open", "retrying"])
            )
        )
    )
    open_incidents = len(open_incident_rows)
    if any(item.data_quality == "unavailable" for item in open_incident_rows):
        status = "unavailable"
    elif any(item.status == "down" for item in source_items):
        status = "unavailable"
    elif open_incidents or any(item.status == "partial" for item in source_items):
        status = "degraded"
    elif source_items:
        status = "healthy"
    else:
        status = "unknown"
    return CollectionHealthRead(status=status, open_incident_count=open_incidents, sources=source_items)


@router.get("/companies/{company_id}/feature-windows", response_model=list[FeatureWindowRead])
def list_feature_windows(
    company_id: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=192, ge=1, le=2000),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[CompanyFeatureWindow]:
    _user_company(db, company_id, auth.user_id)
    query = select(CompanyFeatureWindow).where(CompanyFeatureWindow.company_id == company_id)
    if date_from:
        query = query.where(CompanyFeatureWindow.window_start >= date_from)
    if date_to:
        query = query.where(CompanyFeatureWindow.window_start < date_to)
    return list(db.scalars(query.order_by(CompanyFeatureWindow.window_start.desc()).limit(limit)))


@router.get("/companies/{company_id}/daily-summaries", response_model=list[DailySummaryRead])
def list_daily_summaries(
    company_id: int,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[DailySummaryRead]:
    _user_company(db, company_id, auth.user_id)
    settings = get_settings()
    now = datetime.now(SEOUL)
    cutoff_date, cutoff = seoul_period_start(days, now=now)
    stored = {
        item.summary_date: item
        for item in db.scalars(
            select(CompanyDailySummary).where(
                CompanyDailySummary.company_id == company_id,
                CompanyDailySummary.summary_date >= cutoff_date,
            )
        )
    }

    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    article_day = seoul_day_bucket(article_time).label("day")
    sentiment = func.lower(func.coalesce(NewsArticle.sentiment_label, ""))
    article_rows = {
        row["day"].date(): row
        for row in db.execute(
            select(
                article_day,
                func.count(CompanyArticleMatch.article_id).label("article_count"),
                func.count(CompanyArticleMatch.article_id)
                .filter(sentiment.in_(["positive", "긍정"]))
                .label("positive_article_count"),
                func.count(CompanyArticleMatch.article_id)
                .filter(sentiment.in_(["neutral", "중립"]))
                .label("neutral_article_count"),
                func.count(CompanyArticleMatch.article_id)
                .filter(sentiment.in_(["negative", "부정"]))
                .label("negative_article_count"),
            )
            .select_from(CompanyArticleMatch)
            .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
            .where(
                CompanyArticleMatch.company_id == company_id,
                article_time >= cutoff,
            )
            .group_by(article_day)
        ).mappings()
    }

    risk_day = seoul_day_bucket(RiskEvent.opened_at).label("day")
    risk_rows = {
        row[0].date(): row[1]
        for row in db.execute(
            select(risk_day, func.count(RiskEvent.id))
            .where(
                RiskEvent.company_id == company_id,
                RiskEvent.opened_at >= cutoff,
                RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
            )
            .group_by(risk_day)
        )
    }

    risk_article_rows = {
        row[0].date(): row[1]
        for row in db.execute(
            select(
                article_day,
                func.count(func.distinct(ArticleRiskAssessment.article_id)),
            )
            .select_from(ArticleRiskAssessment)
            .join(NewsArticle, NewsArticle.id == ArticleRiskAssessment.article_id)
            .where(
                ArticleRiskAssessment.company_id == company_id,
                ArticleRiskAssessment.decision == "risk",
                ArticleRiskAssessment.risk_probability
                >= settings.article_risk_candidate_threshold,
                article_time >= cutoff,
            )
            .group_by(article_day)
        )
    }

    results: list[DailySummaryRead] = []
    today = now.date()
    for offset in range(days):
        summary_date = today - timedelta(days=offset)
        materialized = stored.get(summary_date)
        live = article_rows.get(summary_date, {})
        results.append(
            DailySummaryRead(
                company_id=company_id,
                summary_date=summary_date,
                article_count=int(live.get("article_count", 0)),
                risk_article_count=int(risk_article_rows.get(summary_date, 0)),
                positive_article_count=int(live.get("positive_article_count", 0)),
                neutral_article_count=int(live.get("neutral_article_count", 0)),
                negative_article_count=int(live.get("negative_article_count", 0)),
                story_count=materialized.story_count if materialized else 0,
                amplification_count=materialized.amplification_count if materialized else 0,
                publisher_count=materialized.publisher_count if materialized else 0,
                positive_probability=materialized.positive_probability if materialized else None,
                neutral_probability=materialized.neutral_probability if materialized else None,
                negative_probability=materialized.negative_probability if materialized else None,
                risk_event_count=int(risk_rows.get(summary_date, 0)),
                unavailable_window_count=materialized.unavailable_window_count if materialized else 0,
                partial_window_count=materialized.partial_window_count if materialized else 0,
            )
        )
    return results
