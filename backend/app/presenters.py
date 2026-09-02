"""Shared ORM-to-API projections that keep router contracts consistent."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ArticleRiskAssessment,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
)
from app.schemas import RiskEventRead
from app.services.story_risk import source_domain


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
        article_id=event.article_id,
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
