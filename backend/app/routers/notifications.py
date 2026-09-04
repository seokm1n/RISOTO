"""Read-only unified notifications for risks and promotion-ready models."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.config import get_settings
from app.database import get_db
from app.models import Company, NewsArticle, RiskEvent, RiskEventArticle
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


@router.get("/notifications", response_model=NotificationListRead)
def list_notifications(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> NotificationListRead:
    """Return one notification per eligible risk story owned by the user."""
    settings = get_settings()
    eligible_story_event_ids = (
        select(RiskEventArticle.risk_event_id)
        .group_by(RiskEventArticle.risk_event_id)
        .having(
            func.count(func.distinct(RiskEventArticle.article_id))
            >= settings.story_event_min_articles
        )
    )
    risk_rows = db.execute(
        select(RiskEvent, Company)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            Company.user_id == auth.user_id,
            RiskEvent.status.in_(("open", "monitoring")),
            RiskEvent.event_source == "story_v2",
            RiskEvent.story_cluster_id.is_not(None),
            RiskEvent.id.in_(eligible_story_event_ids),
        )
    ).all()
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
        ))

    items = sorted(
        risk_items,
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    return NotificationListRead(
        items=items,
        total=len(items),
        risk_count=len(risk_items),
        model_promotion_count=0,
    )
