"""Model promotion and human-approved response draft APIs."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ModelOperationCheck,
    ModelVersion,
    ResponseDraft,
    RiskEvent,
)
from app.schemas import (
    ModelOperationCheckRead,
    ModelTrainingReadinessRead,
    ModelVersionRead,
    RiskDetectionStatusRead,
    ResponseDraftRead,
    ResponseDraftReview,
)
from app.services.model_operations import (
    build_training_readiness,
    ensure_daily_model_check,
)
from app.services.model_governance import evaluate_model_promotion
from app.services.response_generation import generate_response_draft
from app.services.review_identity import INTERNAL_REVIEW_ACTOR
from app.services.risk_analysis import resolve_production_risk_detector


router = APIRouter(tags=["governance"])


@router.get("/model-versions", response_model=list[ModelVersionRead])
def list_model_versions(
    task: str | None = None,
    db: Session = Depends(get_db),
) -> list[ModelVersion]:
    query = select(ModelVersion)
    if task:
        query = query.where(ModelVersion.task == task)
    return list(db.scalars(query.order_by(ModelVersion.created_at.desc())))


@router.get("/risk-detection-status", response_model=RiskDetectionStatusRead)
def get_risk_detection_status(
    db: Session = Depends(get_db),
) -> RiskDetectionStatusRead:
    """Report final-risk availability without training or promoting a model."""
    runtime = resolve_production_risk_detector(db)
    if not runtime.available:
        messages = {
            "production_lightgbm_not_registered": (
                "운영 LightGBM이 등록되지 않아 위험 판정을 수행하지 않습니다."
            ),
            "artifact_unavailable": (
                "운영 LightGBM 아티팩트를 사용할 수 없어 위험 판정을 수행하지 않습니다."
            ),
            "artifact_contract_invalid": (
                "운영 LightGBM 아티팩트 계약이 올바르지 않아 위험 판정을 수행하지 않습니다."
            ),
            "production_isolation_forest_not_registered": (
                "호환되는 운영 Isolation Forest가 없어 위험 판정을 수행하지 않습니다."
            ),
            "isolation_artifact_unavailable": (
                "운영 Isolation Forest 아티팩트를 사용할 수 없어 위험 판정을 수행하지 않습니다."
            ),
            "isolation_artifact_contract_invalid": (
                "운영 Isolation Forest 아티팩트 계약이 올바르지 않아 위험 판정을 수행하지 않습니다."
            ),
            "isolation_dependency_manifest_invalid": (
                "LightGBM의 Isolation Forest 의존성 정보가 불완전하여 위험 판정을 수행하지 않습니다."
            ),
            "isolation_dependency_mismatch": (
                "LightGBM과 운영 Isolation Forest 버전이 호환되지 않아 위험 판정을 수행하지 않습니다."
            ),
        }
        reason = runtime.reason or "artifact_contract_invalid"
        return RiskDetectionStatusRead(
            risk_detection_status="unavailable",
            reason=reason,
            message=messages[reason],
            model_id=runtime.version.id if runtime.version else None,
            model_version=runtime.version.version if runtime.version else None,
            model_state="unavailable",
        )
    assert runtime.version is not None
    configured_state = str((runtime.version.thresholds or {}).get("model_state", "production"))
    model_state = configured_state if configured_state in {"production", "provisional"} else "production"
    return RiskDetectionStatusRead(
        risk_detection_status="available",
        reason=None,
        message="운영 LightGBM으로 위험 판정을 수행하고 있습니다.",
        model_id=runtime.version.id,
        model_version=runtime.version.version,
        model_state=model_state,
    )


@router.get("/model-training-readiness", response_model=ModelTrainingReadinessRead)
def model_training_readiness(db: Session = Depends(get_db)) -> dict:
    """Expose candidate-training gates without launching a GPU job."""
    return build_training_readiness(db)


@router.get("/model-monitoring", response_model=ModelOperationCheckRead)
def latest_model_monitoring_check(db: Session = Depends(get_db)) -> ModelOperationCheck:
    """Return today's persisted quality, label-distribution and drift check."""
    return ensure_daily_model_check(db)


@router.post("/model-monitoring/check", response_model=ModelOperationCheckRead)
def rerun_model_monitoring_check(db: Session = Depends(get_db)) -> ModelOperationCheck:
    """Recompute today's report after an operator changes labels or collection state."""
    return ensure_daily_model_check(db, force=True)


@router.post("/model-versions/{model_id}/promote", response_model=ModelVersionRead)
def promote_model(model_id: int, db: Session = Depends(get_db)) -> ModelVersion:
    model = db.get(ModelVersion, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="모델 버전을 찾을 수 없습니다.")
    eligibility = evaluate_model_promotion(db, model)
    if not eligibility.allowed:
        raise HTTPException(status_code=409, detail=eligibility.blocker)
    current = list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.task == model.task,
                ModelVersion.status == "production",
                ModelVersion.id != model.id,
            )
        )
    )
    now = datetime.now(timezone.utc)
    for item in current:
        item.status = "retired"
        item.retired_at = now
    if eligibility.target_model_state is not None:
        thresholds = dict(model.thresholds or {})
        thresholds["model_state"] = eligibility.target_model_state
        model.thresholds = thresholds
    model.status = "production"
    model.promoted_at = now
    model.retired_at = None
    db.commit()
    db.refresh(model)
    return model


@router.post("/risk-events/{risk_event_id}/response-drafts", response_model=ResponseDraftRead)
def create_response_draft(
    risk_event_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> ResponseDraft:
    if db.get(RiskEvent, risk_event_id) is None:
        raise HTTPException(status_code=404, detail="위험 이벤트를 찾을 수 없습니다.")
    try:
        return generate_response_draft(risk_event_id, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/risk-events/{risk_event_id}/response-drafts", response_model=list[ResponseDraftRead])
def list_response_drafts(
    risk_event_id: int,
    db: Session = Depends(get_db),
) -> list[ResponseDraft]:
    return list(
        db.scalars(
            select(ResponseDraft)
            .where(ResponseDraft.risk_event_id == risk_event_id)
            .order_by(ResponseDraft.created_at.desc())
        )
    )


def _review_draft(
    draft_id: int,
    state: str,
    payload: ResponseDraftReview,
    db: Session,
) -> ResponseDraft:
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="대응 초안을 찾을 수 없습니다.")
    draft.approval_state = state
    draft.reviewed_by = INTERNAL_REVIEW_ACTOR
    draft.reviewed_at = datetime.now(timezone.utc)
    draft.review_notes = payload.notes
    event = db.get(RiskEvent, draft.risk_event_id)
    if event is not None:
        event.approval_state = state
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/response-drafts/{draft_id}/approve", response_model=ResponseDraftRead)
def approve_response_draft(
    draft_id: int,
    payload: ResponseDraftReview,
    db: Session = Depends(get_db),
) -> ResponseDraft:
    return _review_draft(draft_id, "approved", payload, db)


@router.post("/response-drafts/{draft_id}/reject", response_model=ResponseDraftRead)
def reject_response_draft(
    draft_id: int,
    payload: ResponseDraftReview,
    db: Session = Depends(get_db),
) -> ResponseDraft:
    return _review_draft(draft_id, "rejected", payload, db)
