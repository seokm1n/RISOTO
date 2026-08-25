"""Blind article labeling and event-level risk ground-truth APIs."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.database import get_db
from app.models import (
    ArticleFilterResult,
    ArticleLabel,
    Company,
    RawNewsArticle,
    RiskEvent,
    RiskEventLabel,
)
from app.presenters import risk_event_read
from app.services.risk_ground_truth import (
    apply_authoritative_risk_label,
    validate_risk_label_evidence,
)
from app.services.review_identity import INTERNAL_REVIEW_ACTOR
from app.schemas import (
    ArticleLabelCreate,
    ArticleLabelRead,
    ArticleReviewCandidate,
    RiskEventLabelCreate,
    RiskEventLabelRead,
    RiskEventRead,
)


router = APIRouter(prefix="/reviews", tags=["reviews"])


def _review_priority(result: ArticleFilterResult, raw: RawNewsArticle) -> float:
    """Prefer uncertainty plus likely rare advertisement/negative examples."""
    uncertainty = 1.0 - float(result.confidence or 0.0)
    advertisement = float(result.advertising_score or 0.0)
    text = f"{raw.title} {raw.summary or ''}".casefold()
    negative_hint = 1.0 if any(
        token in text
        for token in ("사고", "유출", "논란", "소송", "리콜", "파업", "적자", "중단")
    ) else 0.0
    return round(uncertainty + 0.5 * advertisement + 0.35 * negative_hint, 6)


@router.get("/articles", response_model=list[ArticleReviewCandidate])
def article_review_candidates(
    company_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[ArticleReviewCandidate]:
    """Return blind candidates without exposing current AI labels or scores."""
    latest_ids = (
        select(func.max(ArticleFilterResult.id).label("id"))
        .group_by(ArticleFilterResult.company_id, ArticleFilterResult.raw_article_id)
        .subquery()
    )
    query = (
        select(ArticleFilterResult, RawNewsArticle, Company)
        .join(latest_ids, latest_ids.c.id == ArticleFilterResult.id)
        .join(RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id)
        .join(Company, Company.id == ArticleFilterResult.company_id)
        .where(
            Company.workspace_id == auth.workspace_id,
            ~exists(
                select(ArticleLabel.id).where(
                    ArticleLabel.company_id == ArticleFilterResult.company_id,
                    ArticleLabel.raw_article_id == ArticleFilterResult.raw_article_id,
                    ArticleLabel.status.in_(["confirmed", "adjudicated"]),
                )
            )
        )
        .order_by(ArticleFilterResult.filtered_at.desc())
        .limit(min(2000, limit * 8))
    )
    if company_id is not None:
        query = query.where(ArticleFilterResult.company_id == company_id)
    rows = db.execute(query).all()
    candidates = [
        ArticleReviewCandidate(
            company_id=company.id,
            company_name=company.name,
            raw_article_id=raw.id,
            source=raw.source,
            title=raw.title,
            summary=raw.summary,
            url=raw.url,
            published_at=raw.published_at,
            review_priority=_review_priority(result, raw),
        )
        for result, raw, company in rows
    ]
    candidates.sort(key=lambda item: item.review_priority, reverse=True)
    return candidates[:limit]


@router.post("/articles", response_model=ArticleLabelRead)
def save_article_label(
    payload: ArticleLabelCreate,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> ArticleLabel:
    valid_candidate = db.scalar(
        select(ArticleFilterResult.id)
        .join(Company, Company.id == ArticleFilterResult.company_id)
        .where(
            Company.workspace_id == auth.workspace_id,
            ArticleFilterResult.company_id == payload.company_id,
            ArticleFilterResult.raw_article_id == payload.raw_article_id,
        ).limit(1)
    )
    if valid_candidate is None:
        raise HTTPException(status_code=404, detail="해당 기업의 관리 대상 기사 후보를 찾을 수 없습니다.")
    label = db.scalar(
        select(ArticleLabel).where(
            ArticleLabel.company_id == payload.company_id,
            ArticleLabel.raw_article_id == payload.raw_article_id,
            ArticleLabel.annotator == INTERNAL_REVIEW_ACTOR,
        )
    )
    values = payload.model_dump()
    values["annotator"] = INTERNAL_REVIEW_ACTOR
    if payload.relevance_label == "irrelevant":
        values["sentiment_label"] = "not_applicable"
    if label is None:
        label = ArticleLabel(**values)
        db.add(label)
    else:
        for key, value in values.items():
            setattr(label, key, value)
        label.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(label)
    return label


@router.get("/risk-events", response_model=list[RiskEventRead])
def risk_review_candidates(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[RiskEventRead]:
    events = list(
        db.scalars(
            select(RiskEvent)
            .join(Company, Company.id == RiskEvent.company_id)
            .where(
                Company.workspace_id == auth.workspace_id,
                RiskEvent.status != "legacy_candidate",
                ~exists(
                    select(RiskEventLabel.id).where(
                        RiskEventLabel.risk_event_id == RiskEvent.id,
                        RiskEventLabel.status.in_(["confirmed", "adjudicated"]),
                    )
                ),
            )
            .order_by(RiskEvent.opened_at.desc())
            .limit(limit)
        )
    )
    return [risk_event_read(db, event) for event in events]


@router.post("/risk-events/{risk_event_id}", response_model=RiskEventLabelRead)
def save_risk_event_label(
    risk_event_id: int,
    payload: RiskEventLabelCreate,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> RiskEventLabel:
    event = db.scalar(
        select(RiskEvent)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            RiskEvent.id == risk_event_id,
            Company.workspace_id == auth.workspace_id,
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="위험 이벤트를 찾을 수 없습니다.")
    try:
        evidence_article_ids = validate_risk_label_evidence(
            db,
            event,
            is_risk=payload.is_risk,
            event_start=payload.event_start,
            event_end=payload.event_end,
            risk_types=payload.risk_types,
            evidence_article_ids=payload.evidence_article_ids,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    label = db.scalar(
        select(RiskEventLabel).where(
            RiskEventLabel.risk_event_id == risk_event_id,
            RiskEventLabel.annotator == INTERNAL_REVIEW_ACTOR,
        )
    )
    values = payload.model_dump()
    values["annotator"] = INTERNAL_REVIEW_ACTOR
    values["risk_types"] = list(dict.fromkeys(payload.risk_types))
    values["evidence_article_ids"] = evidence_article_ids
    if label is None:
        label = RiskEventLabel(risk_event_id=risk_event_id, **values)
        db.add(label)
    else:
        for key, value in values.items():
            setattr(label, key, value)
        label.reviewed_at = datetime.now(timezone.utc)
    db.flush()
    apply_authoritative_risk_label(db, event)
    db.commit()
    db.refresh(label)
    return label
