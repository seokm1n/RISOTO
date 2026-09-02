"""Model status, quality monitoring and human-approved response draft APIs."""

from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_admin, require_auth
from app.config import get_settings
from app.database import get_db
from app.models import (
    Company,
    ModelOperationCheck,
    ModelVersion,
    ResponseDraft,
    RiskEvent,
)
from app.schemas import (
    LlmLabelingStatusRead,
    ModelOperationCheckRead,
    ModelRuntimeStatusRead,
    ModelTrainingReadinessRead,
    ModelVersionRead,
    RiskDetectionStatusRead,
    ResponseDraftRead,
    ResponseDraftReview,
    ResponseGenerationAcceptedRead,
)
from app.services.llm_labeling import llm_labeling_status, run_llm_labeling_backlog
from app.services.model_operations import (
    build_training_readiness,
    ensure_daily_model_check,
)
from app.services.response_engine import (
    SCHEMA_VERSION,
    enqueue_response_draft,
    generate_response_draft,
)
from app.services.risk_analysis import resolve_production_risk_detector


router = APIRouter(tags=["governance"])


@router.get("/model-versions", response_model=list[ModelVersionRead])
def list_model_versions(
    task: str | None = None,
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> list[ModelVersion]:
    query = select(ModelVersion)
    if task:
        query = query.where(ModelVersion.task == task)
    return list(db.scalars(query.order_by(ModelVersion.created_at.desc())))


@router.get("/risk-detection-status", response_model=RiskDetectionStatusRead)
def get_risk_detection_status(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
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


@router.get("/model-runtime-status", response_model=ModelRuntimeStatusRead)
def get_model_runtime_status(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> ModelRuntimeStatusRead:
    """Expose the four independently operated model stages."""
    settings = get_settings()
    advertising_path = Path(settings.pretrained_relevance_model_path).expanduser()
    sentiment_path = Path(settings.pretrained_sentiment_model_path).expanduser()
    relevance_model = db.scalar(
        select(ModelVersion)
        .where(
            ModelVersion.task == "topical_relevance",
            ModelVersion.status == "production",
        )
        .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.id.desc())
        .limit(1)
    )
    relevance_path = (
        Path(relevance_model.artifact_path).expanduser()
        if relevance_model is not None
        else None
    )
    lightgbm_path = Path(settings.external_lightgbm_model_path).expanduser()
    lightgbm_available = lightgbm_path.is_file()
    risk_runtime = resolve_production_risk_detector(db)
    return ModelRuntimeStatusRead(
        article_filter_version=settings.article_filter_version,
        article_filter_ai_enabled=settings.article_filter_ai_enabled,
        advertising_model_name=advertising_path.name if advertising_path.name else None,
        advertising_model_available=advertising_path.is_dir(),
        relevance_model_name=(
            relevance_model.version
            if relevance_model is not None
            else None
        ),
        relevance_model_available=(
            relevance_path is not None and relevance_path.is_dir()
        ),
        sentiment_model_name=sentiment_path.name if sentiment_path.name else None,
        sentiment_model_available=sentiment_path.is_dir(),
        external_lightgbm_model_name=lightgbm_path.name if lightgbm_path.name else None,
        external_lightgbm_model_available=lightgbm_available,
        external_lightgbm_message=(
            "exports LightGBM과 호환 Isolation Forest가 운영 판정에 연결되었습니다."
            if risk_runtime.available
            else "LightGBM 파일은 찾았지만 운영 레지스트리 연결을 확인해야 합니다."
            if lightgbm_available
            else "외부 LightGBM 파일을 찾지 못했습니다."
        ),
    )


@router.get("/model-training-readiness", response_model=ModelTrainingReadinessRead)
def model_training_readiness(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> dict:
    """Expose candidate-training gates without launching a GPU job."""
    return build_training_readiness(db)


@router.get("/model-monitoring", response_model=ModelOperationCheckRead)
def latest_model_monitoring_check(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> ModelOperationCheck:
    """Return today's persisted quality, label-distribution and drift check."""
    return ensure_daily_model_check(db)


@router.post("/model-monitoring/check", response_model=ModelOperationCheckRead)
def rerun_model_monitoring_check(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> ModelOperationCheck:
    """Recompute today's report after an operator changes labels or collection state."""
    return ensure_daily_model_check(db, force=True)


@router.get("/llm-labeling/status", response_model=LlmLabelingStatusRead)
def get_llm_labeling_status(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> dict:
    """Report automatic LLM labeling throughput and this month's human audit agreement."""
    return llm_labeling_status(db)


@router.post("/llm-labeling/run", response_model=LlmLabelingStatusRead)
def trigger_llm_labeling_backlog(
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> dict:
    """Manually catch up any articles the automatic per-company trigger missed."""
    run_llm_labeling_backlog(db)
    return llm_labeling_status(db)


@router.post("/risk-events/{risk_event_id}/response-drafts", response_model=ResponseDraftRead)
def create_response_draft(
    risk_event_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> ResponseDraft:
    event = db.scalar(
        select(RiskEvent)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            RiskEvent.id == risk_event_id,
            Company.user_id == auth.user_id,
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="위험 이벤트를 찾을 수 없습니다.")
    existing_v3 = db.scalar(
        select(ResponseDraft.id)
        .where(
            ResponseDraft.risk_event_id == risk_event_id,
            ResponseDraft.schema_version == SCHEMA_VERSION,
            ResponseDraft.user_id == auth.user_id,
        )
        .limit(1)
    )
    if event.event_source == "story_v2" and not (
        existing_v3 is not None
        or event.response_generation_status in {"failed", "deferred"}
    ):
        raise HTTPException(
            status_code=409,
            detail="최초 대응방안은 자동 생성 중입니다. 완료 후 다시 시도해 주세요.",
        )
    try:
        draft = generate_response_draft(risk_event_id, force=True if event.event_source == "story_v2" else force)
        return draft
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/risk-events/{risk_event_id}/response-generation",
    response_model=ResponseGenerationAcceptedRead,
    status_code=202,
)
def start_response_generation(
    risk_event_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> ResponseGenerationAcceptedRead:
    """개별 사건의 대응방안 생성을 중복 없이 비동기로 시작한다."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"response-generation-request:{risk_event_id}"},
        )
    query = (
        select(RiskEvent)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            RiskEvent.id == risk_event_id,
            Company.user_id == auth.user_id,
        )
    )
    if db.get_bind().dialect.name != "postgresql":
        query = query.with_for_update()
    event = db.scalar(query)
    if event is None:
        raise HTTPException(status_code=404, detail="위험 이벤트를 찾을 수 없습니다.")
    if event.event_source != "story_v2":
        raise HTTPException(
            status_code=409,
            detail="스토리 사건에서만 대응방안을 생성할 수 있습니다.",
        )

    current_status = event.response_generation_status
    if current_status in {"pending", "generating"}:
        db.commit()
        return ResponseGenerationAcceptedRead(
            risk_event_id=event.id,
            status=current_status,
        )
    if current_status == "generated" and not force:
        db.commit()
        return ResponseGenerationAcceptedRead(
            risk_event_id=event.id,
            status="generated",
        )

    event.response_generation_status = "pending"
    event.response_generation_error = None
    db.commit()
    enqueue_response_draft(event.id, force=force or current_status == "failed")
    return ResponseGenerationAcceptedRead(
        risk_event_id=event.id,
        status="pending",
    )


@router.get("/risk-events/{risk_event_id}/response-drafts", response_model=list[ResponseDraftRead])
def list_response_drafts(
    risk_event_id: int,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[ResponseDraft]:
    event_exists = db.scalar(
        select(RiskEvent.id)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            RiskEvent.id == risk_event_id,
            Company.user_id == auth.user_id,
        )
    )
    if event_exists is None:
        raise HTTPException(status_code=404, detail="위험 이벤트를 찾을 수 없습니다.")
    return list(
        db.scalars(
            select(ResponseDraft)
            .where(
                ResponseDraft.risk_event_id == risk_event_id,
                ResponseDraft.user_id == auth.user_id,
            )
            .order_by(ResponseDraft.created_at.desc())
        )
    )


def _review_draft(
    draft_id: int,
    state: str,
    payload: ResponseDraftReview,
    db: Session,
    auth: CurrentAuth,
) -> ResponseDraft:
    draft = db.scalar(
        select(ResponseDraft)
        .join(RiskEvent, RiskEvent.id == ResponseDraft.risk_event_id)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            ResponseDraft.id == draft_id,
            ResponseDraft.user_id == auth.user_id,
            Company.user_id == auth.user_id,
        )
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="대응 초안을 찾을 수 없습니다.")
    if draft.approval_state != "draft":
        raise HTTPException(status_code=409, detail="이미 검토가 완료된 대응 초안입니다.")
    draft.approval_state = state
    draft.reviewed_by_user_id = auth.user_id
    draft.reviewed_by = auth.user.email
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
    auth: CurrentAuth = Depends(require_auth),
) -> ResponseDraft:
    return _review_draft(draft_id, "approved", payload, db, auth)


@router.post("/response-drafts/{draft_id}/reject", response_model=ResponseDraftRead)
def reject_response_draft(
    draft_id: int,
    payload: ResponseDraftReview,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> ResponseDraft:
    return _review_draft(draft_id, "rejected", payload, db, auth)
