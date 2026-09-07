"""One period story population for grouping, judgments and briefing statistics."""

from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ArticleFilterResult, ArticleRiskAssessment, CompanyArticleMatch, NewsArticle,
    RiskEvent, RiskEventArticle, StoryClusterArticle, StoryRiskScore,
)
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES
from app.services.period_aggregation import SEOUL, seoul_date_range
from app.services.story_model_runtime import resolve_story_risk_runtime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _sentiment(articles: list[NewsArticle]) -> str:
    aliases = {"긍정": "positive", "중립": "neutral", "부정": "negative"}
    values = {kind: [] for kind in ("positive", "neutral", "negative")}
    for article in articles:
        label = (article.sentiment_label or "").lower()
        label = aliases.get(label, label)
        for kind in values:
            probability = getattr(article, f"{kind}_probability")
            if probability is not None:
                values[kind].append(float(probability))
            elif label in values:
                values[kind].append(float(label == kind))
    if not all(values.values()):
        return "pending"
    scores = {kind: mean(probabilities) for kind, probabilities in values.items()}
    if scores["positive"] > max(scores["neutral"], scores["negative"]):
        return "positive"
    if scores["negative"] > max(scores["positive"], scores["neutral"]):
        return "negative"
    return "neutral"


def canonical_accepted_article_ids(company_id: int):
    """Curated articles referenced by a current accepted raw-article decision."""
    latest_ids = (select(func.max(ArticleFilterResult.id))
                  .where(ArticleFilterResult.company_id == company_id)
                  .group_by(ArticleFilterResult.raw_article_id))
    return select(ArticleFilterResult.curated_article_id).where(
        ArticleFilterResult.id.in_(latest_ids), ArticleFilterResult.decision == "accepted",
        ArticleFilterResult.curated_article_id.is_not(None),
    )


def period_issue_cluster_ids(company_id: int, period_start: datetime, period_end: datetime, *, analysis_only=False):
    """Select issue IDs by their latest company-linked article timestamp."""
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    return (
        select(StoryClusterArticle.story_cluster_id)
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
        .where(CompanyArticleMatch.company_id == company_id)
        .where(NewsArticle.id.in_(canonical_accepted_article_ids(company_id)) if analysis_only else True)
        .group_by(StoryClusterArticle.story_cluster_id)
        .having(func.max(article_time) >= period_start, func.max(article_time) < period_end)
    )


def load_period_story_cohort(
    db: Session, company_id: int, start_date: date, end_date: date, *, settings=None, runtime=None,
    include_unjudged: bool = True,
) -> list[dict]:
    """Return every issue whose latest article falls in the selected Seoul dates.

    Issue membership includes company-linked articles with current accepted
    decisions. Related evidence spans all dates; the latest
    publication date (or creation date when absent) controls the issue's date and
    chart bucket. Missing judgments never become synthetic negative scores.
    """
    settings = settings or get_settings()
    minimum = max(1, settings.story_event_min_articles)
    start, end = seoul_date_range(start_date, end_date)
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    groups = defaultdict(list)
    for article, cluster_id in db.execute(
        select(NewsArticle, StoryClusterArticle.story_cluster_id)
        .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
        .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
        .where(CompanyArticleMatch.company_id == company_id,
               NewsArticle.id.in_(canonical_accepted_article_ids(company_id)),
               StoryClusterArticle.story_cluster_id.in_(period_issue_cluster_ids(company_id, start, end, analysis_only=True)))
        .order_by(article_time, NewsArticle.id)
    ):
        groups[cluster_id].append(article)
    groups = {cluster_id: articles for cluster_id, articles in groups.items() if len(articles) >= minimum}
    if not groups:
        return []

    eligible_event_ids = (
        select(RiskEventArticle.risk_event_id)
        .group_by(RiskEventArticle.risk_event_id)
        .having(func.count(func.distinct(RiskEventArticle.article_id)) >= minimum)
    )
    events = {}
    for event in db.scalars(
        select(RiskEvent).where(
            RiskEvent.company_id == company_id, RiskEvent.event_source == "story_v2",
            RiskEvent.story_cluster_id.in_(groups),
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
            RiskEvent.id.in_(eligible_event_ids),
        ).order_by(
            func.coalesce(RiskEvent.last_evidence_at, RiskEvent.last_seen_at,
                          RiskEvent.opened_at, RiskEvent.detected_at).desc(),
            RiskEvent.id.desc(),
        )
    ):
        events.setdefault(event.story_cluster_id, event)

    model_enabled = settings.story_risk_engine_enabled and settings.story_risk_model_enabled
    scores = {}
    assessments = defaultdict(list)
    if model_enabled:
        runtime = runtime or resolve_story_risk_runtime(settings)
        scores = {score.story_cluster_id: score for score in db.scalars(
            select(StoryRiskScore).where(StoryRiskScore.company_id == company_id,
                                        StoryRiskScore.story_cluster_id.in_(groups))
        )}
    else:
        for assessment, cluster_id in db.execute(
            select(ArticleRiskAssessment, StoryClusterArticle.story_cluster_id)
            .join(CompanyArticleMatch, (CompanyArticleMatch.article_id == ArticleRiskAssessment.article_id)
                  & (CompanyArticleMatch.company_id == ArticleRiskAssessment.company_id))
            .join(StoryClusterArticle, StoryClusterArticle.article_id == CompanyArticleMatch.article_id)
            .where(CompanyArticleMatch.company_id == company_id,
                   CompanyArticleMatch.article_id.in_(canonical_accepted_article_ids(company_id)),
                   StoryClusterArticle.story_cluster_id.in_(groups))
        ):
            assessments[cluster_id].append(assessment)

    cohort = []
    for cluster_id, articles in groups.items():
        event = events.get(cluster_id)
        score = scores.get(cluster_id)
        classification, pending_reason = "pending", None
        risk_probability, anomaly_score, model_version, model_state = None, None, None, "unavailable"
        if event is not None:
            classification = "risk"
            risk_probability, anomaly_score = event.risk_probability, event.anomaly_score
            model_version, model_state = event.model_version, event.model_state
        elif model_enabled:
            if score is None:
                pending_reason = "missing_score"
            elif runtime.available and score.model_version != runtime.version:
                pending_reason = "stale_model"
            elif score.is_risk:
                pending_reason = "risk_event_pending"
            else:
                classification = "non_risk"
                risk_probability, anomaly_score = score.risk_probability, score.anomaly_score
                model_version, model_state = score.model_version, score.model_state
        else:
            rows = assessments.get(cluster_id, [])
            if not rows:
                pending_reason = "missing_assessment"
            elif any(row.decision == "risk" for row in rows):
                pending_reason = "risk_event_pending"
            elif {row.article_id for row in rows} != {article.id for article in articles}:
                pending_reason = "missing_assessment"
            elif any(row.decision != "non_risk" for row in rows):
                pending_reason = "missing_assessment"
            else:
                classification = "non_risk"
                risk_probability = max(row.risk_probability for row in rows)
                model_version, model_state = rows[0].model_version, "provisional"
        times = [_utc(article.published_at or article.created_at) for article in articles]
        cohort.append(dict(
            story_cluster_id=cluster_id,
            summary_date=times[-1].astimezone(SEOUL).date(),
            classification=classification, sentiment=_sentiment(articles),
            risk_event_id=event.id if event else None,
            risk_event_status=event.status if event else None,
            pending_reason=pending_reason,
            article_ids=[article.id for article in articles],
            first_evidence_at=times[0], last_evidence_at=times[-1],
            risk_probability=risk_probability, anomaly_score=anomaly_score,
            model_version=model_version, model_state=model_state,
        ))
    return cohort if include_unjudged else [row for row in cohort if row["classification"] != "pending"]
