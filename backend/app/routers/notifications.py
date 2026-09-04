"""Unified notifications and persistent per-user read state."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.config import get_settings
from app.database import get_db
from app.models import (
    Company,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskNotificationRead,
)
from app.schemas import NotificationItemRead, NotificationListRead


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


def _risk_notification_rows(db: Session, user_id: int) -> list[tuple[RiskEvent, Company]]:
    """Return the current risk events eligible for this user's notification center."""
    settings = get_settings()
    eligible_story_event_ids = (
        select(RiskEventArticle.risk_event_id)
        .group_by(RiskEventArticle.risk_event_id)
        .having(
            func.count(func.distinct(RiskEventArticle.article_id))
            >= settings.story_event_min_articles
        )
    )
    return list(db.execute(
        select(RiskEvent, Company)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            Company.user_id == user_id,
            RiskEvent.status.in_(("open", "monitoring")),
            RiskEvent.event_source == "story_v2",
            RiskEvent.story_cluster_id.is_not(None),
            RiskEvent.id.in_(eligible_story_event_ids),
        )
    ).all())


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
    risk_event_ids = [event.id for event, _company in risk_rows]
    read_event_ids = set(db.scalars(
        select(RiskNotificationRead.risk_event_id).where(
            RiskNotificationRead.user_id == auth.user_id,
            RiskNotificationRead.risk_event_id.in_(risk_event_ids),
        )
    ).all()) if risk_event_ids else set()
    risk_items = []
    for event, company in risk_rows:
        article_title = _representative_article_title(db, event)
        risk_items.append(NotificationItemRead(
            id=f"risk:{event.id}",
            type="risk",
            title=f"{company.name} 위험 스토리",
            message=event.summary
            or article_title
            or f"{event.severity} 수준의 위험 스토리가 현재 열려 있습니다.",
            created_at=event.opened_at,
            company_id=company.id,
            risk_event_id=event.id,
            is_read=event.id in read_event_ids,
        ))

    items = sorted(
        risk_items,
        key=lambda item: (item.created_at, item.id),
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
        event.id for event, _company in _risk_notification_rows(db, auth.user_id)
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
