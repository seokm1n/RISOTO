"""Shared ORM-to-API projections that keep router contracts consistent."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NewsArticle, RiskEvent, RiskEventArticle, RiskEventType
from app.schemas import RiskEventRead


def risk_event_read(db: Session, event: RiskEvent) -> RiskEventRead:
    """Project a window-centric risk event with all evidence and type links."""
    evidence_rows = db.execute(
        select(RiskEventArticle, NewsArticle)
        .join(NewsArticle, NewsArticle.id == RiskEventArticle.article_id)
        .where(RiskEventArticle.risk_event_id == event.id)
        .order_by(RiskEventArticle.evidence_score.desc(), NewsArticle.published_at.desc().nullslast())
    ).all()
    if not evidence_rows and event.article_id is not None:
        article = db.get(NewsArticle, event.article_id)
        if article is not None:
            evidence_rows = [(None, article)]
    types = list(
        db.scalars(
            select(RiskEventType)
            .where(RiskEventType.risk_event_id == event.id)
            .order_by(RiskEventType.is_primary.desc(), RiskEventType.probability.desc())
        )
    )
    primary_article = evidence_rows[0][1] if evidence_rows else None
    return RiskEventRead(
        id=event.id,
        company_id=event.company_id,
        article_id=event.article_id,
        article_title=primary_article.title if primary_article else None,
        article_url=primary_article.url if primary_article else None,
        feature_window_id=event.feature_window_id,
        anomaly_score=event.anomaly_score,
        risk_probability=event.risk_probability,
        severity=event.severity,
        status=event.status,
        primary_type=event.primary_type,
        risk_types=[
            {
                "risk_type": item.risk_type,
                "probability": item.probability,
                "is_primary": item.is_primary,
                "evidence": item.evidence,
            }
            for item in types
        ],
        evidence_articles=[
            {
                "article_id": article.id,
                "title": article.title,
                "url": article.url,
                "source": article.source,
                "published_at": article.published_at,
                "evidence_score": link.evidence_score if link else 0.0,
            }
            for link, article in evidence_rows
        ],
        summary=event.summary,
        model_version=event.model_version,
        model_state=event.model_state,
        approval_state=event.approval_state,
        opened_at=event.opened_at,
        last_seen_at=event.last_seen_at,
        closed_at=event.closed_at,
        detected_at=event.detected_at,
    )
