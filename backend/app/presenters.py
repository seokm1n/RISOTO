"""Shared ORM-to-API projections that keep router contracts consistent."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ArticleRiskAssessment,
    NewsArticle,
    RawNewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
)
from app.schemas import RiskEventRead
from app.services.story_risk import source_domain



def article_collection_times(db: Session, articles: list[NewsArticle]) -> dict[int, datetime]:
    """Prefer the original collection timestamp over the processed article creation time."""
    raw_ids = {article.raw_article_id for article in articles if article.raw_article_id is not None}
    raw_times = dict(db.execute(
        select(RawNewsArticle.id, RawNewsArticle.collected_at)
        .where(RawNewsArticle.id.in_(raw_ids))
    ).all()) if raw_ids else {}
    return {
        article.id: raw_times.get(article.raw_article_id) or article.created_at
        for article in articles
    }

def risk_event_read(
    db: Session,
    event: RiskEvent,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    included_article_ids: list[int] | None = None,
) -> RiskEventRead:
    """Project a risk event, optionally limiting evidence and its counts to a period."""
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    period_filters = []
    if period_start is not None:
        period_filters.append(article_time >= period_start)
    if period_end is not None:
        period_filters.append(article_time < period_end)
    if included_article_ids is not None:
        period_filters.append(NewsArticle.id.in_(included_article_ids))
        evidence_query = (
            select(RiskEventArticle, NewsArticle)
            .select_from(NewsArticle)
            .outerjoin(RiskEventArticle, (RiskEventArticle.article_id == NewsArticle.id)
                       & (RiskEventArticle.risk_event_id == event.id))
            .where(*period_filters)
        )
    else:
        evidence_query = (
            select(RiskEventArticle, NewsArticle)
            .join(NewsArticle, NewsArticle.id == RiskEventArticle.article_id)
            .where(RiskEventArticle.risk_event_id == event.id, *period_filters)
        )
    evidence_rows = db.execute(evidence_query.order_by(
        RiskEventArticle.evidence_score.desc().nullslast(), NewsArticle.published_at.desc().nullslast(),
    )).all()
    if not evidence_rows and event.article_id is not None:
        article = db.scalar(select(NewsArticle).where(
            NewsArticle.id == event.article_id, *period_filters,
        ))
        if article is not None:
            evidence_rows = [(None, article)]
    collected_times = article_collection_times(db, [article for _link, article in evidence_rows])
    article_ids = [article.id for _link, article in evidence_rows]
    assessments = {
        item.article_id: item
        for item in db.scalars(
            select(ArticleRiskAssessment).where(
                ArticleRiskAssessment.company_id == event.company_id,
                ArticleRiskAssessment.article_id.in_(article_ids),
            )
        )
    } if article_ids else {}
    candidate_threshold = get_settings().article_risk_candidate_threshold

    def evidence_role(article_id: int) -> str:
        assessment = assessments.get(article_id)
        if event.event_source != "story_v2":
            return "trigger"
        if (
            assessment is not None
            and assessment.decision == "risk"
            and assessment.risk_probability >= candidate_threshold
        ):
            return "trigger"
        return "context"
    types = list(
        db.scalars(
            select(RiskEventType)
            .where(RiskEventType.risk_event_id == event.id)
            .order_by(RiskEventType.is_primary.desc(), RiskEventType.probability.desc())
        )
    )
    primary_article = evidence_rows[0][1] if evidence_rows else None
    evidence_domains = {
        source_domain(article.original_url or article.url)
        for _link, article in evidence_rows
    } - {"unknown"}
    risk_rows = [
        (link, article)
        for link, article in evidence_rows
        if evidence_role(article.id) == "trigger"
    ]
    risk_domains = {
        source_domain(article.original_url or article.url)
        for _link, article in risk_rows
    } - {"unknown"}
    return RiskEventRead(
        id=event.id,
        company_id=event.company_id,
        article_id=primary_article.id if period_filters and primary_article else (
            None if period_filters else event.article_id
        ),
        article_title=primary_article.title if primary_article else None,
        article_url=primary_article.url if primary_article else None,
        feature_window_id=event.feature_window_id,
        story_cluster_id=event.story_cluster_id,
        event_source=event.event_source,
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
                "source_domain": source_domain(article.original_url or article.url),
                "published_at": article.published_at,
                "created_at": article.created_at,
                "collected_at": collected_times.get(article.id),
                "evidence_role": evidence_role(article.id),
                "evidence_score": link.evidence_score if link else 0.0,
                "risk_probability": link.risk_probability if link else None,
                "relevance_score": link.relevance_score if link else None,
                "type_match_score": link.type_match_score if link else None,
                "source_credibility": link.source_credibility if link else None,
                "representativeness": link.representativeness if link else None,
            }
            for link, article in evidence_rows
        ],
        risk_article_count=len(risk_rows),
        risk_source_count=len(risk_domains),
        evidence_article_count=len(evidence_rows),
        source_count=len(evidence_domains),
        summary=event.summary,
        model_version=event.model_version,
        model_state=event.model_state,
        approval_state=event.approval_state,
        opened_at=event.opened_at,
        last_seen_at=event.last_seen_at,
        closed_at=event.closed_at,
        last_evidence_at=event.last_evidence_at,
        evidence_revision=event.evidence_revision,
        response_generation_status=event.response_generation_status,
        response_generation_error=event.response_generation_error,
        closure_reason=event.closure_reason,
        detected_at=event.detected_at,
    )
