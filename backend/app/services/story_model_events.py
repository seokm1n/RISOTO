"""Persist story model scores and reconcile automatic events in one transaction.

The binary model governs event eligibility. Article type annotations explain
evidence only and never gate model inference or replace its probabilities.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ArticleFilterResult, ArticleRiskAssessment, Company, CompanyArticleMatch, CompanyKeyword,
    NewsArticle, RiskEvent, RiskEventLabel, RiskEventType, StoryCluster, StoryClusterArticle, StoryRiskScore,
)
from app.risk_taxonomy import RISK_TYPES
from app.services.risk_analysis import classify_risk_types
from app.services.story_model_runtime import build_story_snapshot, resolve_story_risk_runtime


def _type_evidence(assessment, article):
    from app.services.story_risk import source_credibility, source_domain

    if assessment is not None and assessment.decision != "failed":
        return assessment
    domain = source_domain(article.original_url or article.url)
    return SimpleNamespace(
        type_scores=classify_risk_types([article.title + " " + (article.summary or "")]),
        risk_probability=0.0, relevance_score=1.0, source_credibility=source_credibility(domain),
        source_domain=domain,
    )


def _sync_types(db, event, aggregate_scores, primary):
    desired = {kind: value for kind, value in aggregate_scores.items() if value >= .35 or kind == primary}
    existing = {link.risk_type: link for link in db.scalars(
        select(RiskEventType).where(RiskEventType.risk_event_id == event.id))}
    changed = set(existing) != set(desired)
    for kind in set(existing) - set(desired):
        db.delete(existing[kind])
    for kind, value in desired.items():
        link = existing.get(kind)
        if link is None:
            link = RiskEventType(risk_event_id=event.id, risk_type=kind, probability=value,
                                 evidence={"source": "article_assessment_or_keywords", "role": "type_explanation_only"})
            db.add(link)
        elif link.probability != value or link.is_primary != (kind == primary):
            changed = True
        link.probability = value
        link.is_primary = kind == primary
    return changed


def _event_values(event):
    return tuple(getattr(event, name) for name in (
        "status", "risk_probability", "anomaly_score", "severity", "primary_type", "summary", "article_id",
        "feature_window_id", "model_version", "model_state", "opened_at", "last_seen_at", "last_evidence_at",
        "closed_at", "closure_reason", "evidence_revision",
    ))


def refresh_story_model_events(
    db: Session, company_id: int, cluster_ids=None, *, settings=None,
    as_of: datetime | None = None, enqueue_drafts: bool = False,
) -> dict:
    """Score every eligible group before writing; caller commits and queues drafts.

    No labels or external LLM services are created here. Current accepted story
    membership is the same source selection as the training exporter. Snapshot
    features freeze after 24 hours; all currently accepted articles still update
    evidence and the existing minimum-article/calendar lifecycle policy.
    """
    from app.services.story_risk import (
        EVENT_KEY_PREFIX, _article_time, _event_lock, _has_authoritative_closure,
        _story_event_inactivity_cutoff, _sync_event_evidence,
    )

    settings = settings or get_settings()
    captured = as_of or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    runtime = resolve_story_risk_runtime(settings)
    if not runtime.available:
        raise RuntimeError(f"Story risk model unavailable ({runtime.reason}): {runtime.message}")
    totals = dict(scored=0, risk_predictions=0, events_changed=0, events_created=0,
                  events_withdrawn=0, protected_events=0, event_ids_to_enqueue=[])
    selected = None if cluster_ids is None else sorted(set(cluster_ids))
    if selected == []:
        return totals
    _event_lock(db, f"story-risk-company:{company_id}")
    company = db.get(Company, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} was not found")
    aliases = list(db.scalars(select(CompanyKeyword.value).where(
        CompanyKeyword.company_id == company_id, CompanyKeyword.keyword_type == "alias").order_by(CompanyKeyword.id)))
    accepted = select(ArticleFilterResult.id).where(
        ArticleFilterResult.company_id == company_id,
        ArticleFilterResult.curated_article_id == NewsArticle.id,
        ArticleFilterResult.decision == "accepted",
    ).exists()
    query = (
        select(NewsArticle, StoryClusterArticle, ArticleRiskAssessment)
        .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
        .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
        .outerjoin(ArticleRiskAssessment, (ArticleRiskAssessment.article_id == NewsArticle.id)
                   & (ArticleRiskAssessment.company_id == company_id))
        .where(CompanyArticleMatch.company_id == company_id, NewsArticle.created_at <= captured, accepted)
        .order_by(StoryClusterArticle.story_cluster_id, NewsArticle.created_at, NewsArticle.id)
    )
    event_query = select(RiskEvent).where(
        RiskEvent.company_id == company_id, RiskEvent.event_source == "story_v2",
        RiskEvent.story_cluster_id.is_not(None),
    )
    score_query = select(StoryRiskScore).where(StoryRiskScore.company_id == company_id)
    if selected is not None:
        query = query.where(StoryClusterArticle.story_cluster_id.in_(selected))
        event_query = event_query.where(RiskEvent.story_cluster_id.in_(selected))
        score_query = score_query.where(StoryRiskScore.story_cluster_id.in_(selected))
    groups = defaultdict(list)
    for article, link, assessment in db.execute(query):
        groups[link.story_cluster_id].append((article, link, assessment))
    snapshots = [build_story_snapshot(company_id, company.name, aliases,
                                     [row[0] for row in rows], story_id=sid, as_of=captured)
                 for sid, rows in groups.items()]
    predictions = runtime.predict(snapshots)
    if len(predictions) != len(snapshots) or any(not result.get("available") for result in predictions):
        failure = next((p.get("message") for p in predictions if not p.get("available")), "Incomplete prediction batch")
        raise RuntimeError(f"Story risk prediction failed before persistence: {failure}")
    by_prediction = {snapshot["story_id"]: (snapshot, prediction)
                     for snapshot, prediction in zip(snapshots, predictions)}
    events = {event.story_cluster_id: event for event in db.scalars(event_query)
              if event.event_key == f"{EVENT_KEY_PREFIX}:{company_id}:{event.story_cluster_id}"}
    scores = {score.story_cluster_id: score for score in db.scalars(score_query)}
    protected = set(db.scalars(select(RiskEventLabel.risk_event_id).join(
        RiskEvent, RiskEvent.id == RiskEventLabel.risk_event_id).where(
            RiskEvent.company_id == company_id, RiskEventLabel.status.in_(["confirmed", "adjudicated"]))))
    clusters = {cluster.id: cluster for cluster in db.scalars(
        select(StoryCluster).where(StoryCluster.id.in_(list(groups))))} if groups else {}
    for sid, (snapshot, prediction) in by_prediction.items():
        score = scores.get(sid)
        score_values = {key: prediction[key] for key in (
            "risk_probability", "is_risk", "anomaly_score", "anomaly_percentile", "threshold", "model_version",
            "model_state", "artifact_sha256", "snapshot_hash",
        )}
        if score is None:
            score = StoryRiskScore(company_id=company_id, story_cluster_id=sid)
            db.add(score)
        if any(getattr(score, key) != value for key, value in score_values.items()):
            for key, value in score_values.items():
                setattr(score, key, value)
            score.as_of = datetime.fromisoformat(snapshot["as_of"])
            score.scored_at = captured
            score.article_count = len(snapshot["articles"])
            score.input_snapshot = snapshot
        totals["scored"] += 1
        totals["risk_predictions"] += int(prediction["is_risk"])
    # Every prediction has succeeded before the first score/event is flushed.
    for sid in sorted(set(groups) | set(events)):
        event = events.get(sid)
        if event is not None and (event.id in protected or _has_authoritative_closure(db, event)):
            totals["protected_events"] += 1
            continue
        rows = groups.get(sid, [])
        prediction = by_prediction.get(sid, (None, None))[1]
        enough_articles = len(rows) >= max(1, settings.story_event_min_articles)
        qualifies = prediction is not None and prediction["is_risk"] and enough_articles
        if not qualifies:
            if event is None:
                continue
            before = _event_values(event)
            reason = "story_model_insufficient_evidence" if not enough_articles else "story_model_non_risk"
            if event.status != "legacy_candidate" or event.closure_reason != reason:
                event.closed_at = captured
                totals["events_withdrawn"] += 1
            event.status = "legacy_candidate"
            event.closure_reason = reason
            event.response_generation_status = "idle"
            event.response_generation_error = None
            if prediction is not None:
                event.risk_probability = prediction["risk_probability"]
                event.anomaly_score = prediction["anomaly_score"]
                event.model_version = prediction["model_version"]
                event.model_state = prediction["model_state"]
                event.feature_window_id = None
            totals["events_changed"] += int(before != _event_values(event))
            continue
        evidence = [(_type_evidence(assessment, article), article, link) for article, link, assessment in rows]
        aggregate = {kind: max(float((assessment.type_scores or {}).get(kind, 0))
                              for assessment, _article, _link in evidence) for kind in RISK_TYPES}
        primary, maximum = max(aggregate.items(), key=lambda item: item[1])
        if maximum <= 0:
            # The binary model provides no taxonomy. Retain an existing type,
            # otherwise explicitly leave it unclassified rather than inventing one.
            primary = event.primary_type if event is not None else None
        evidence_times = sorted(_article_time(article) for article, _link, _assessment in rows)
        qualification_time = evidence_times[max(1, settings.story_event_min_articles) - 1]
        latest = evidence_times[-1]
        representative = next((article for article, link, _assessment in rows if link.is_representative), rows[0][0])
        cluster = clusters.get(sid)
        created = event is None
        before = _event_values(event) if event is not None else None
        old_model = event.model_version if event is not None else None
        old_status = event.status if event is not None else None
        if event is None:
            event = RiskEvent(company_id=company_id, story_cluster_id=sid,
                              event_key=f"{EVENT_KEY_PREFIX}:{company_id}:{sid}", event_source="story_v2",
                              anomaly_score=prediction["anomaly_score"], severity="warning", status="open",
                              approval_state="draft", evidence_revision=0, last_response_revision=0)
            db.add(event)
        elif event.status == "legacy_candidate" or (event.status == "closed" and latest >= _story_event_inactivity_cutoff(
                captured, settings.story_event_inactivity_days)):
            event.status = "monitoring"
        if event.status != "closed":
            event.closed_at = None
            event.closure_reason = None
        event.consecutive_below = 0
        event.feature_window_id = None
        event.risk_probability = prediction["risk_probability"]
        event.anomaly_score = prediction["anomaly_score"]
        event.model_version = prediction["model_version"]
        event.model_state = prediction["model_state"]
        # A binary classifier's certainty is not an estimate of business impact.
        event.severity = "warning"
        event.article_id = representative.id
        event.primary_type = primary
        event.summary = cluster.representative_title if cluster is not None else representative.title
        event.opened_at = qualification_time
        event.last_seen_at = latest
        event.last_evidence_at = latest
        db.flush()
        new_official = _sync_event_evidence(db, event, evidence, primary)
        type_changed = _sync_types(db, event, aggregate, primary)
        changed = created or before != _event_values(event) or type_changed
        if changed:
            totals["events_changed"] += 1
        totals["events_created"] += int(created)
        material_change = (created or (old_status in {"closed", "legacy_candidate"} and event.status != "closed")
                           or old_model != event.model_version or type_changed or new_official
                           or (before is not None and before[-1] != event.evidence_revision))
        if material_change and not created and event.evidence_revision <= event.last_response_revision:
            event.evidence_revision = event.last_response_revision + 1
        if (material_change and event.status in {"open", "monitoring", "acknowledged"}
                and event.evidence_revision > event.last_response_revision):
            event.response_generation_status = "pending" if enqueue_drafts else "deferred"
            event.response_generation_error = None
            if enqueue_drafts:
                totals["event_ids_to_enqueue"].append(event.id)
    db.flush()
    return totals
