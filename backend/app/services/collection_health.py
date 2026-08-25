"""Collection source health, incident aggregation and non-blocking webhook delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    CollectionAttempt,
    CollectionIncident,
    CollectionJob,
    Company,
    NotificationDelivery,
)


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret)(\s*[:=]\s*)[^\s,&]+"),
    re.compile(r"(?i)(client[_-]?secret)(\s*[:=]\s*)[^\s,&]+"),
    re.compile(
        r"(?i)(authorization)(\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;&]+"
    ),
)
SEOUL = ZoneInfo("Asia/Seoul")


def floor_window(value: datetime, minutes: int = 15) -> datetime:
    """Align a timezone-aware datetime to a fixed tumbling window."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    minute = value.minute - value.minute % minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def sanitize_error(value: str | None) -> str:
    """Return a bounded operator summary without credentials, SQL or parameters."""
    cleaned = (value or "unknown collection error").replace("\n", " ").strip()
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(r"\1\2[REDACTED]", cleaned)
    lowered = cleaned.casefold()
    database_markers = (
        "sqlalchemy.exc",
        "psycopg.errors",
        "integrityerror",
        "uniqueviolation",
        "duplicate key value",
        "violates unique constraint",
        "database integrity conflict",
        "database operation failed",
        "[sql:",
        "[parameters:",
    )
    if any(marker in lowered for marker in database_markers):
        prefix = "pipeline: " if lowered.startswith("pipeline:") else ""
        if (
            "duplicate key" in lowered
            or "uniqueviolation" in lowered
            or "database integrity conflict" in lowered
        ):
            return f"{prefix}데이터 저장 충돌"
        return f"{prefix}데이터베이스 작업 실패"
    # Some SDK exceptions append SQLAlchemy-style diagnostic blocks even when
    # the leading message is safe. Never expose those blocks to UI/webhooks.
    for marker in (" [sql:", " [parameters:", " (background on this error"):
        index = cleaned.casefold().find(marker)
        if index >= 0:
            cleaned = cleaned[:index].rstrip()
    return cleaned[:800]


def error_code(value: str | None) -> str:
    """Map provider text to a stable fingerprint component."""
    text = (value or "").casefold()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "401" in text or "403" in text or "unauthorized" in text:
        return "authentication"
    if "429" in text or "rate" in text:
        return "rate_limit"
    if "connection" in text or "network" in text:
        return "network"
    if "key" in text and "설정" in text:
        return "configuration"
    if "database integrity conflict" in text or "데이터 저장 충돌" in text:
        return "database_conflict"
    return "provider_error"


def _incident_fingerprint(data_quality: str, sources: list[str], codes: list[str]) -> str:
    material = "|".join([data_quality, *sorted(sources), *sorted(codes)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_attempts(
    db: Session,
    job: CollectionJob,
    source_stats: dict[str, dict[str, Any]],
    scheduled_for: datetime,
    attempt_number: int,
) -> list[CollectionAttempt]:
    """Persist one aggregate attempt per configured source for a collection job."""
    attempts: list[CollectionAttempt] = []
    now = datetime.now(timezone.utc)
    for source in job.sources:
        stats = source_stats.get(source, {})
        success_count = int(stats.get("successful_query_count", 0))
        messages = [sanitize_error(item) for item in stats.get("errors", []) if item]
        attempt = CollectionAttempt(
            user_id=job.user_id,
            job_id=job.id,
            company_id=job.company_id,
            source=source,
            scheduled_for=scheduled_for,
            attempt_number=attempt_number,
            status="succeeded" if success_count > 0 else "failed",
            query_count=int(stats.get("query_count", 0)),
            successful_query_count=success_count,
            fetched_count=int(stats.get("fetched_count", 0)),
            error_code=error_code(messages[0]) if messages else None,
            error_message="; ".join(messages)[:1000] if messages else None,
            started_at=stats.get("started_at") or job.started_at or now,
            completed_at=stats.get("completed_at") or now,
        )
        db.add(attempt)
        attempts.append(attempt)
    db.flush()
    return attempts


def data_quality_for(attempts: list[CollectionAttempt]) -> str:
    """Differentiate a valid empty result from partial and total source failure."""
    if not attempts or all(item.status == "failed" for item in attempts):
        return "unavailable"
    if any(item.status == "failed" for item in attempts):
        return "partial"
    return "complete"


def _consecutive_failures(db: Session, company_id: int, source: str, limit: int) -> int:
    rows = list(
        db.scalars(
            select(CollectionAttempt)
            .where(CollectionAttempt.company_id == company_id, CollectionAttempt.source == source)
            .where(CollectionAttempt.attempt_number == 0)
            .order_by(CollectionAttempt.started_at.desc(), CollectionAttempt.id.desc())
            .limit(limit)
        )
    )
    count = 0
    for row in rows:
        if row.status != "failed":
            break
        count += 1
    return count


def _queue_notification(
    db: Session,
    incident: CollectionIncident,
    event_kind: str,
    settings: Settings,
) -> None:
    if not settings.collection_alert_webhook_url:
        return
    local_slot = incident.scheduled_for.astimezone(SEOUL).strftime("%H:%M")
    payload = {
        "type": f"collection_{event_kind}",
        "message": (
            f"{local_slot} 수집 구간 복구"
            if event_kind == "recovered"
            else f"{local_slot} 수집 구간 {'실패' if incident.data_quality == 'unavailable' else '일부 실패'}"
        ),
        "incident_id": incident.id,
        "status": incident.status,
        "data_quality": incident.data_quality,
        "severity": incident.severity,
        "scheduled_for": incident.scheduled_for.isoformat(),
        "detected_at": incident.detected_at.isoformat(),
        "affected_company_ids": incident.affected_company_ids,
        "sources": incident.sources,
        "retry_count": incident.retry_count,
        "retry_status": "scheduled" if incident.next_retry_at else "finished",
        "next_retry_at": incident.next_retry_at.isoformat() if incident.next_retry_at else None,
        "error_summary": sanitize_error(incident.error_summary),
    }
    existing = db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.incident_id == incident.id,
            NotificationDelivery.event_kind == event_kind,
        )
    )
    if existing is not None:
        if existing.status in {"pending", "failed"}:
            existing.payload = payload
        return
    db.add(
        NotificationDelivery(
            incident_id=incident.id,
            channel="webhook",
            event_kind=event_kind,
            endpoint=settings.collection_alert_webhook_url,
            payload=payload,
            status="pending",
            next_retry_at=datetime.now(timezone.utc),
        )
    )
    # The same root cause can merge another company before the outer transaction
    # commits. Flush now so the next merge updates this delivery instead of
    # queuing a duplicate with the same (incident, event_kind) key.
    db.flush()
    if event_kind == "failed":
        incident.notified_at = datetime.now(timezone.utc)


def _upsert_incident(
    db: Session,
    *,
    company_id: int,
    scheduled_for: datetime,
    data_quality: str,
    failed_attempts: list[CollectionAttempt],
    notify: bool,
    settings: Settings,
) -> CollectionIncident:
    user_id = failed_attempts[0].user_id
    sources = sorted({item.source for item in failed_attempts})
    codes = sorted({item.error_code or "provider_error" for item in failed_attempts})
    fingerprint = _incident_fingerprint(data_quality, sources, codes)
    incident = db.scalar(
        select(CollectionIncident).where(
            CollectionIncident.user_id == user_id,
            CollectionIncident.fingerprint == fingerprint,
            CollectionIncident.scheduled_for == scheduled_for,
            CollectionIncident.status.in_(["open", "retrying"]),
        )
    )
    now = datetime.now(timezone.utc)
    summary = "; ".join(
        f"{item.source}: {sanitize_error(item.error_message)}" for item in failed_attempts
    )[:2000]
    if incident is None:
        first_delay = settings.collection_retry_delays[0] if data_quality == "unavailable" else None
        incident = CollectionIncident(
            user_id=user_id,
            fingerprint=fingerprint,
            status="retrying" if first_delay else "open",
            data_quality=data_quality,
            severity="critical" if data_quality == "unavailable" else "warning",
            scheduled_for=scheduled_for,
            detected_at=now,
            last_seen_at=now,
            affected_company_ids=[company_id],
            sources=sources,
            error_summary=summary,
            retry_count=0,
            next_retry_at=now + timedelta(seconds=first_delay) if first_delay else None,
        )
        db.add(incident)
        db.flush()
    else:
        affected = list(incident.affected_company_ids or [])
        if company_id not in affected:
            affected.append(company_id)
            incident.affected_company_ids = sorted(affected)
        incident.last_seen_at = now
        incident.error_summary = summary or incident.error_summary
    if notify:
        _queue_notification(db, incident, "failed", settings)
    return incident


def recover_company_incidents(
    db: Session,
    company_id: int,
    successful_sources: list[str],
    settings: Settings,
) -> list[int]:
    """Remove a recovered company from matching incidents and close fully recovered incidents."""
    if not successful_sources:
        return []
    company = db.get(Company, company_id)
    if company is None:
        return []
    incidents = list(
        db.scalars(
            select(CollectionIncident).where(
                CollectionIncident.user_id == company.user_id,
                CollectionIncident.status.in_(["open", "retrying"]),
            )
        )
    )
    recovered: list[int] = []
    successful_set = set(successful_sources)
    for incident in incidents:
        affected = list(incident.affected_company_ids or [])
        if company_id not in affected or not set(incident.sources).issubset(successful_set):
            continue
        affected.remove(company_id)
        incident.affected_company_ids = affected
        if affected:
            continue
        incident.status = "recovered"
        incident.recovered_at = datetime.now(timezone.utc)
        incident.next_retry_at = None
        _queue_notification(db, incident, "recovered", settings)
        recovered.append(incident.id)
    return recovered


def evaluate_attempts(
    db: Session,
    attempts: list[CollectionAttempt],
    scheduled_for: datetime,
    settings: Settings | None = None,
    *,
    manage_incidents: bool = True,
) -> tuple[str, int | None]:
    """Return window quality and optionally create/recover alert incidents."""
    settings = settings or get_settings()
    quality = data_quality_for(attempts)
    if not attempts or not manage_incidents:
        return quality, None
    company_id = attempts[0].company_id
    failed = [item for item in attempts if item.status == "failed"]
    succeeded = [item.source for item in attempts if item.status == "succeeded"]
    recover_company_incidents(db, company_id, succeeded, settings)
    if quality == "complete":
        return quality, None
    if quality == "unavailable":
        incident = _upsert_incident(
            db,
            company_id=company_id,
            scheduled_for=scheduled_for,
            data_quality=quality,
            failed_attempts=failed,
            notify=True,
            settings=settings,
        )
        return quality, incident.id

    qualifying = [
        item
        for item in failed
        if _consecutive_failures(
            db,
            company_id,
            item.source,
            settings.partial_failure_consecutive_threshold,
        )
        >= settings.partial_failure_consecutive_threshold
    ]
    if not qualifying:
        incident = _upsert_incident(
            db,
            company_id=company_id,
            scheduled_for=scheduled_for,
            data_quality=quality,
            failed_attempts=failed,
            notify=False,
            settings=settings,
        )
        return quality, incident.id
    incident = _upsert_incident(
        db,
        company_id=company_id,
        scheduled_for=scheduled_for,
        data_quality=quality,
        failed_attempts=qualifying,
        notify=True,
        settings=settings,
    )
    return quality, incident.id


def record_pipeline_failure(
    company_id: int,
    scheduled_for: datetime,
    error: str,
    *,
    requested_from: datetime | None = None,
    requested_to: datetime | None = None,
    settings: Settings | None = None,
    dispatch: bool = True,
) -> int | None:
    """Persist an unexpected orchestration failure as an auditable, alertable attempt."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    message = sanitize_error(error)
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            return None
        job = CollectionJob(
            user_id=company.user_id,
            company_id=company_id,
            status="failed",
            job_type="realtime",
            sources=["pipeline"],
            errors=[{"source": "pipeline", "message": message}],
            requested_from=requested_from,
            requested_to=requested_to or now,
            started_at=now,
            completed_at=now,
        )
        db.add(job)
        db.flush()
        attempts = record_attempts(
            db,
            job,
            {
                "pipeline": {
                    "query_count": 0,
                    "successful_query_count": 0,
                    "fetched_count": 0,
                    "errors": [message],
                    "started_at": now,
                    "completed_at": now,
                }
            },
            scheduled_for,
            0,
        )
        _, incident_id = evaluate_attempts(db, attempts, scheduled_for, settings)
        db.commit()
    if dispatch:
        dispatch_pending_notifications(settings)
    return incident_id


def complete_retry(
    incident_id: int,
    resolved_company_ids: list[int],
    settings: Settings | None = None,
) -> None:
    """Advance an outage after successful retries and invalid references are resolved."""
    settings = settings or get_settings()
    with SessionLocal() as db:
        incident = db.get(CollectionIncident, incident_id)
        if incident is None or incident.status not in {"open", "retrying"}:
            return
        remaining = [
            company_id
            for company_id in (incident.affected_company_ids or [])
            if company_id not in set(resolved_company_ids)
        ]
        incident.affected_company_ids = remaining
        now = datetime.now(timezone.utc)
        incident.last_seen_at = now
        if not remaining:
            incident.status = "recovered"
            incident.recovered_at = now
            incident.next_retry_at = None
            _queue_notification(db, incident, "recovered", settings)
        else:
            incident.retry_count += 1
            delays = settings.collection_retry_delays
            if incident.retry_count < len(delays):
                incident.status = "retrying"
                incident.next_retry_at = now + timedelta(seconds=delays[incident.retry_count])
            else:
                incident.status = "acknowledged" if incident.acknowledged_at else "open"
                incident.next_retry_at = None
        db.commit()


def dispatch_pending_notifications(settings: Settings | None = None, limit: int = 20) -> int:
    """Deliver due webhooks with bounded retries; never raise into collection code."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        deliveries = list(
            db.scalars(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.status.in_(["pending", "failed"]),
                    (NotificationDelivery.next_retry_at.is_(None))
                    | (NotificationDelivery.next_retry_at <= now),
                    NotificationDelivery.attempt_count < 5,
                )
                .order_by(NotificationDelivery.created_at)
                .limit(limit)
            )
        )
        delivered = 0
        for item in deliveries:
            item.attempt_count += 1
            try:
                payload = dict(item.payload or {})
                if "error_summary" in payload:
                    payload["error_summary"] = sanitize_error(payload["error_summary"])
                    item.payload = payload
                response = httpx.post(
                    item.endpoint,
                    json=payload,
                    timeout=settings.collection_alert_webhook_timeout_seconds,
                )
                item.response_code = response.status_code
                response.raise_for_status()
            except Exception as exc:  # delivery failures are persisted, not propagated
                item.status = "failed"
                item.error_message = sanitize_error(f"{type(exc).__name__}: {exc}")
                item.next_retry_at = now + timedelta(minutes=min(30, 2 ** item.attempt_count))
            else:
                item.status = "delivered"
                item.error_message = None
                item.delivered_at = now
                item.next_retry_at = None
                delivered += 1
        db.commit()
        return delivered
