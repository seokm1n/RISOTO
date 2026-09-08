"""Unified notifications and persistent per-user read state."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.config import get_settings
from app.database import get_db
from app.models import (
    ArticleFilterResult,
    Company,
    CompanyArticleMatch,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskNotificationRead,
    StoryClusterArticle,
    StoryRiskScore,
)
from app.schemas import NotificationItemRead, NotificationListRead
from app.services.period_aggregation import SEOUL, seoul_date_range
from app.services.story_model_runtime import resolve_story_risk_runtime


router = APIRouter(tags=["notifications"])


def _representative_article_title(db: Session, event: RiskEvent) -> str | None:
    """Return the strongest evidence article title for a risk notification."""
    title = db.scalar(
        select(NewsArticle.title)
        .join(RiskEventArticle, RiskEventArticle.article_id == NewsArticle.id)
        .where(RiskEventArticle.risk_event_id == event.id)
        .order_by(
            RiskEventArticle.evidence_score.desc(),
            NewsArticle.published_at.desc().nullslast(),
            NewsArticle.id.desc(),
        )
        .limit(1)
    )
    if title or event.article_id is None:
        return title
    return db.scalar(select(NewsArticle.title).where(NewsArticle.id == event.article_id))


def _notification_period() -> tuple[datetime, datetime]:
    today = datetime.now(SEOUL).date()
    return seoul_date_range(today - timedelta(days=2), today)


def _risk_notification_rows(db: Session, user_id: int) -> list[tuple[RiskEvent, Company, datetime]]:
    """Show current risk judgments with recent evidence in three Seoul dates."""
    settings = get_settings()
    start, end = _notification_period()
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    event_evidence = (
        select(RiskEventArticle.risk_event_id, func.max(article_time).label("latest_article_at"))
        .join(NewsArticle, NewsArticle.id == RiskEventArticle.article_id)
        .group_by(RiskEventArticle.risk_event_id)
        .having(
            func.count(func.distinct(RiskEventArticle.article_id))
            >= settings.story_event_min_articles
        )
    ).subquery()
    query = (
        select(RiskEvent, Company)
        .join(Company, Company.id == RiskEvent.company_id)
        .join(event_evidence, event_evidence.c.risk_event_id == RiskEvent.id)
        .where(
            Company.user_id == user_id,
            RiskEvent.status.in_(("open", "monitoring")),
            RiskEvent.event_source == "story_v2",
            RiskEvent.story_cluster_id.is_not(None),
        )
    )
    latest_article_at = event_evidence.c.latest_article_at
    if settings.story_risk_engine_enabled and settings.story_risk_model_enabled:
        runtime = resolve_story_risk_runtime(settings)
        if not runtime.available:
            return []
        company_ids = select(Company.id).where(Company.user_id == user_id)
        latest_filter_ids = (
            select(func.max(ArticleFilterResult.id))
            .where(ArticleFilterResult.company_id.in_(company_ids))
            .group_by(ArticleFilterResult.company_id, ArticleFilterResult.raw_article_id)
        )
        accepted = select(
            ArticleFilterResult.company_id, ArticleFilterResult.curated_article_id,
        ).where(
            ArticleFilterResult.id.in_(latest_filter_ids),
            ArticleFilterResult.decision == "accepted",
            ArticleFilterResult.curated_article_id.is_not(None),
        ).distinct().subquery()
        issues = (
            select(CompanyArticleMatch.company_id, StoryClusterArticle.story_cluster_id,
                   func.max(article_time).label("latest_article_at"))
            .select_from(CompanyArticleMatch)
            .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
            .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
            .join(accepted, (accepted.c.company_id == CompanyArticleMatch.company_id)
                  & (accepted.c.curated_article_id == NewsArticle.id))
            .group_by(CompanyArticleMatch.company_id, StoryClusterArticle.story_cluster_id)
            .having(func.count(func.distinct(NewsArticle.id)) >= settings.story_event_min_articles)
        ).subquery()
        query = query.join(
            issues, (issues.c.company_id == RiskEvent.company_id)
            & (issues.c.story_cluster_id == RiskEvent.story_cluster_id),
        ).join(
            StoryRiskScore, (StoryRiskScore.company_id == RiskEvent.company_id)
            & (StoryRiskScore.story_cluster_id == RiskEvent.story_cluster_id),
        ).where(
            StoryRiskScore.is_risk.is_(True),
            StoryRiskScore.model_version == runtime.version,
            RiskEvent.model_version == runtime.version,
        )
        latest_article_at = issues.c.latest_article_at
    rows = db.execute(query.add_columns(latest_article_at).where(
        latest_article_at >= start, latest_article_at < end,
    ).order_by(latest_article_at.desc(), RiskEvent.id.desc())).all()
    # Historical event revisions must not create duplicate notifications.
    seen = set()
    result = []
    for event, company, latest_at in rows:
        key = (company.id, event.story_cluster_id)
        if key not in seen:
            seen.add(key)
            result.append((event, company, latest_at))
    return result


def _mark_risk_events_read(db: Session, user_id: int, risk_event_ids: list[int]) -> None:
    """Insert idempotent read markers for the requested risk events."""
    if not risk_event_ids:
        return
    statement = insert(RiskNotificationRead).values([
        {"user_id": user_id, "risk_event_id": risk_event_id}
        for risk_event_id in risk_event_ids
    ]).on_conflict_do_nothing(index_elements=["user_id", "risk_event_id"])
    db.execute(statement)


@router.get("/notifications", response_model=NotificationListRead)
def list_notifications(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> NotificationListRead:
    """Return one notification per eligible risk story owned by the user."""
    risk_rows = _risk_notification_rows(db, auth.user_id)
    risk_event_ids = [event.id for event, _company, _latest_at in risk_rows]
    read_event_ids = set(db.scalars(
        select(RiskNotificationRead.risk_event_id).where(
            RiskNotificationRead.user_id == auth.user_id,
            RiskNotificationRead.risk_event_id.in_(risk_event_ids),
        )
    ).all()) if risk_event_ids else set()
    risk_items = []
    for event, company, latest_at in risk_rows:
        article_title = _representative_article_title(db, event)
        risk_items.append(NotificationItemRead(
            id=f"risk:{event.id}",
            type="risk",
            title=f"{company.name} 위험 이슈",
            message=event.summary
            or article_title
            or f"{event.severity} 수준의 위험 이슈가 현재 열려 있습니다.",
            created_at=event.opened_at,
            latest_article_at=latest_at,
            company_id=company.id,
            risk_event_id=event.id,
            is_read=event.id in read_event_ids,
        ))

    items = sorted(
        risk_items,
        key=lambda item: (not item.is_read, item.latest_article_at or item.created_at, item.created_at, item.id),
        reverse=True,
    )
    return NotificationListRead(
        items=items,
        total=len(items),
        unread_count=sum(not item.is_read for item in items),
        risk_count=len(risk_items),
        model_promotion_count=0,
    )


@router.post("/notifications/read-all", response_model=NotificationListRead)
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> NotificationListRead:
    """Persist read markers for every notification currently visible to the user."""
    risk_event_ids = [
        event.id for event, _company, _latest_at in _risk_notification_rows(db, auth.user_id)
    ]
    _mark_risk_events_read(db, auth.user_id, risk_event_ids)
    db.commit()
    return list_notifications(db, auth)


@router.post("/notifications/risk/{risk_event_id}/read", response_model=NotificationListRead)
def mark_risk_notification_read(
    risk_event_id: int,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> NotificationListRead:
    """Persist one risk notification as read after verifying user ownership."""
    owned_event_id = db.scalar(
        select(RiskEvent.id)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            RiskEvent.id == risk_event_id,
            Company.user_id == auth.user_id,
        )
    )
    if owned_event_id is None:
        raise HTTPException(status_code=404, detail="위험 알림을 찾을 수 없습니다.")
    _mark_risk_events_read(db, auth.user_id, [owned_event_id])
    db.commit()
    return list_notifications(db, auth)
