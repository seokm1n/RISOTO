"""Collection incident, health, feature-window and daily-summary APIs."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.database import get_db
from app.models import (
    CollectionAttempt,
    CollectionIncident,
    Company,
    CompanyDailySummary,
    CompanyFeatureWindow,
)
from app.schemas import (
    CollectionHealthRead,
    CollectionIncidentPage,
    CollectionIncidentRead,
    CollectionSourceHealthRead,
    DailySummaryRead,
    FeatureWindowRead,
)


router = APIRouter(tags=["operations"])


def _workspace_company(db: Session, company_id: int, workspace_id: int) -> Company:
    company = db.scalar(
        select(Company).where(
            Company.id == company_id,
            Company.workspace_id == workspace_id,
        )
    )
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    return company


@router.get("/collection-incidents", response_model=CollectionIncidentPage)
def list_collection_incidents(
    status: str | None = Query(default=None, pattern="^(open|retrying|recovered|acknowledged)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionIncidentPage:
    query = select(CollectionIncident).where(
        CollectionIncident.workspace_id == auth.workspace_id
    )
    if status:
        query = query.where(CollectionIncident.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        db.scalars(
            query.order_by(CollectionIncident.detected_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return CollectionIncidentPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/collection-incidents/{incident_id}/acknowledge", response_model=CollectionIncidentRead)
def acknowledge_collection_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionIncident:
    incident = db.scalar(
        select(CollectionIncident).where(
            CollectionIncident.id == incident_id,
            CollectionIncident.workspace_id == auth.workspace_id,
        )
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="수집 장애를 찾을 수 없습니다.")
    if incident.status != "recovered" and incident.next_retry_at is None:
        incident.status = "acknowledged"
    incident.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/collection-health", response_model=CollectionHealthRead)
def collection_health(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionHealthRead:
    # `pipeline` is a synthetic orchestration-failure source, not a collector.
    # Its historical attempts remain auditable through incidents, but must not
    # permanently poison the per-collector health list after recovery.
    sources = list(
        db.scalars(
            select(CollectionAttempt.source)
            .where(
                CollectionAttempt.workspace_id == auth.workspace_id,
                CollectionAttempt.source != "pipeline",
            )
            .distinct()
            .order_by(CollectionAttempt.source)
        )
    )
    source_items: list[CollectionSourceHealthRead] = []
    for source in sources:
        attempts = list(
            db.scalars(
                select(CollectionAttempt)
                .where(
                    CollectionAttempt.workspace_id == auth.workspace_id,
                    CollectionAttempt.source == source,
                    CollectionAttempt.attempt_number == 0,
                )
                .order_by(CollectionAttempt.scheduled_for.desc(), CollectionAttempt.id.desc())
                .limit(200)
            )
        )
        by_window: dict[datetime, list[CollectionAttempt]] = {}
        for attempt in attempts:
            by_window.setdefault(attempt.scheduled_for, []).append(attempt)
        window_groups = [by_window[key] for key in sorted(by_window, reverse=True)]
        consecutive = 0
        for group in window_groups:
            if any(attempt.status == "succeeded" for attempt in group):
                break
            consecutive += 1
        latest = attempts[0] if attempts else None
        latest_success = next((item for item in attempts if item.status == "succeeded"), None)
        latest_group = window_groups[0] if window_groups else []
        latest_failed = any(item.status == "failed" for item in latest_group)
        latest_succeeded = any(item.status == "succeeded" for item in latest_group)
        source_items.append(
            CollectionSourceHealthRead(
                source=source,
                status=(
                    "unknown" if latest is None else
                    "partial" if latest_failed and latest_succeeded else
                    "healthy" if latest_succeeded else
                    "partial" if consecutive == 1 else "down"
                ),
                last_attempt_at=latest.completed_at if latest else None,
                last_success_at=latest_success.completed_at if latest_success else None,
                consecutive_failures=consecutive,
            )
        )
    open_incident_rows = list(
        db.scalars(
            select(CollectionIncident).where(
                CollectionIncident.workspace_id == auth.workspace_id,
                CollectionIncident.status.in_(["open", "retrying"])
            )
        )
    )
    open_incidents = len(open_incident_rows)
    if any(item.data_quality == "unavailable" for item in open_incident_rows):
        status = "unavailable"
    elif any(item.status == "down" for item in source_items):
        status = "unavailable"
    elif open_incidents or any(item.status == "partial" for item in source_items):
        status = "degraded"
    elif source_items:
        status = "healthy"
    else:
        status = "unknown"
    return CollectionHealthRead(status=status, open_incident_count=open_incidents, sources=source_items)


@router.get("/companies/{company_id}/feature-windows", response_model=list[FeatureWindowRead])
def list_feature_windows(
    company_id: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=192, ge=1, le=2000),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[CompanyFeatureWindow]:
    _workspace_company(db, company_id, auth.workspace_id)
    query = select(CompanyFeatureWindow).where(CompanyFeatureWindow.company_id == company_id)
    if date_from:
        query = query.where(CompanyFeatureWindow.window_start >= date_from)
    if date_to:
        query = query.where(CompanyFeatureWindow.window_start < date_to)
    return list(db.scalars(query.order_by(CompanyFeatureWindow.window_start.desc()).limit(limit)))


@router.get("/companies/{company_id}/daily-summaries", response_model=list[DailySummaryRead])
def list_daily_summaries(
    company_id: int,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[CompanyDailySummary]:
    _workspace_company(db, company_id, auth.workspace_id)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    return list(
        db.scalars(
            select(CompanyDailySummary)
            .where(
                CompanyDailySummary.company_id == company_id,
                CompanyDailySummary.summary_date >= cutoff,
            )
            .order_by(CompanyDailySummary.summary_date.desc())
        )
    )
