"""Apply the selected story model without re-labeling articles or sending old alerts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    Company, CompanyArticleMatch, NewsArticle, RiskEvent, RiskEventArticle,
    RiskEventLabel, RiskEventType, RiskNotificationRead, StoryClusterArticle, StoryRiskScore,
)
from app.services.story_model_runtime import resolve_story_risk_runtime


def _serialize(rows):
    return [{column.name: getattr(row, column.name) for column in row.__table__.columns} for row in rows]


def reapply_story_model(
    *, company_id: int | None = None, hours: int | None = None,
    enqueue_drafts: bool = False, draft_limit: int | None = None,
    output_dir: str | Path | None = None, settings: Settings | None = None,
) -> dict:
    from app.services.story_model_events import refresh_story_model_events
    from app.services.story_risk import _event_lock, _reconcile_story_event_lifecycle

    settings = settings or get_settings()
    runtime = resolve_story_risk_runtime(settings)
    if not runtime.available:
        raise ValueError(runtime.message)
    now = datetime.now(timezone.utc)
    output = Path(output_dir or (Path("training_data/story_model_applications") / now.strftime("%Y%m%dT%H%M%S%fZ")))
    output.mkdir(parents=True, exist_ok=False)
    report = dict(
        scope="all" if hours is None else f"hours:{hours}", model_version=runtime.version,
        artifact_sha256=runtime.artifact_sha256, model_state="provisional", threshold=runtime.threshold,
        as_of=now.isoformat(), scored=0, risk_predictions=0, events_changed=0, events_created=0,
        events_withdrawn=0, protected_events=0, legacy_migrated=0, stale_closed=0,
        drafts_enqueued=0, notifications_suppressed=0, assessed=0, llm_attempted=0,
        output_dir=str(output), by_company=[],
    )
    enqueue_ids = set()
    with SessionLocal() as db:
        query = select(Company).order_by(Company.id)
        if company_id is not None:
            query = query.where(Company.id == company_id)
        companies = list(db.scalars(query))
        if company_id is not None and not companies:
            raise ValueError(f"Company {company_id} does not exist")
        company_ids = [company.id for company in companies]
        for cid in company_ids:
            _event_lock(db, f"story-risk-company:{cid}")
        previous_events = list(db.scalars(select(RiskEvent).where(RiskEvent.company_id.in_(company_ids))))
        previous_ids = {event.id for event in previous_events}
        backup = dict(
            captured_at=now.isoformat(), company_ids=company_ids,
            risk_events=_serialize(previous_events),
            risk_event_articles=_serialize(db.scalars(select(RiskEventArticle).where(RiskEventArticle.risk_event_id.in_(previous_ids)))),
            risk_event_types=_serialize(db.scalars(select(RiskEventType).where(RiskEventType.risk_event_id.in_(previous_ids)))),
            story_risk_scores=_serialize(db.scalars(select(StoryRiskScore).where(StoryRiskScore.company_id.in_(company_ids)))),
        )
        (output / "before.json").write_text(json.dumps(backup, ensure_ascii=False, default=str), encoding="utf-8")
        reviewed = set(db.scalars(select(RiskEventLabel.risk_event_id).where(
            RiskEventLabel.status.in_(["confirmed", "adjudicated"])
        )))
        if hours is None:
            for event in previous_events:
                obsolete = event.event_source == "window_v1" or (
                    event.event_source == "story_v2" and not (event.event_key or "").startswith("story-v3:")
                )
                if obsolete and event.status not in {"dismissed", "legacy_candidate"} and event.id not in reviewed:
                    event.status = "legacy_candidate"
                    event.closure_reason = "story_model_migration"
                    event.closed_at = event.closed_at or now
                    event.response_generation_status = "idle"
                    event.response_generation_error = None
                    report["legacy_migrated"] += 1
        for company in companies:
            cluster_ids = None
            if hours is not None:
                cluster_ids = list(db.scalars(select(StoryClusterArticle.story_cluster_id)
                    .join(NewsArticle, NewsArticle.id == StoryClusterArticle.article_id)
                    .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
                    .where(CompanyArticleMatch.company_id == company.id,
                        func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= now - timedelta(hours=hours))
                    .distinct()))
            result = refresh_story_model_events(
                db, company.id, cluster_ids, settings=settings, as_of=now, enqueue_drafts=enqueue_drafts,
            )
            enqueue_ids.update(result.pop("event_ids_to_enqueue"))
            for key in ("scored", "risk_predictions", "events_changed", "events_created", "events_withdrawn", "protected_events"):
                report[key] += result[key]
            report["by_company"].append({"company_id": company.id, "company_name": company.name, **result})
            lifecycle = _reconcile_story_event_lifecycle(db, settings, company_id=company.id, now=now)
            report["stale_closed"] += lifecycle["closed"]
        db.flush()
        # Historical model application must not create a backlog of unread alerts.
        new_events = list(db.scalars(select(RiskEvent).where(
            RiskEvent.company_id.in_(company_ids), RiskEvent.id.notin_(previous_ids),
            RiskEvent.model_version == runtime.version,
        )))
        owners = {company.id: company.user_id for company in companies}
        for event in new_events:
            if not enqueue_drafts:
                db.add(RiskNotificationRead(user_id=owners[event.company_id], risk_event_id=event.id, read_at=now))
                report["notifications_suppressed"] += 1
        db.commit()
    if enqueue_drafts:
        from app.services.response_engine import enqueue_response_draft

        selected = sorted(enqueue_ids)[:draft_limit] if draft_limit else sorted(enqueue_ids)
        for event_id in selected:
            enqueue_response_draft(event_id, auto=True)
        report["drafts_enqueued"] = len(selected)
    report["companies"] = len(companies)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report
