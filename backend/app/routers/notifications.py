"""Read-only unified notifications for risks and promotion-ready models."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Company, ModelVersion, RiskEvent
from app.schemas import NotificationItemRead, NotificationListRead
from app.services.model_governance import (
    READINESS_GATED_TASKS,
    evaluate_model_promotion,
)
from app.services.model_operations import build_training_readiness


router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationListRead)
def list_notifications(db: Session = Depends(get_db)) -> NotificationListRead:
    """Merge current risk events with candidates that pass today's promotion gate."""
    risk_rows = db.execute(
        select(RiskEvent, Company)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(RiskEvent.status.in_(("open", "monitoring")))
    ).all()
    risk_items = [
        NotificationItemRead(
            id=f"risk:{event.id}",
            type="risk",
            title=f"{company.name} 위험 이벤트",
            message=event.summary
            or f"{event.severity} 수준의 위험 이벤트가 현재 열려 있습니다.",
            created_at=event.opened_at,
            company_id=company.id,
            risk_event_id=event.id,
        )
        for event, company in risk_rows
    ]

    candidates = list(
        db.scalars(
            select(ModelVersion).where(ModelVersion.status == "candidate")
        )
    )
    readiness = (
        build_training_readiness(db)
        if any(model.task in READINESS_GATED_TASKS for model in candidates)
        else None
    )
    model_items: list[NotificationItemRead] = []
    for model in candidates:
        eligibility = evaluate_model_promotion(db, model, readiness=readiness)
        if not eligibility.allowed:
            continue
        model_items.append(
            NotificationItemRead(
                id=f"model:{model.id}",
                type="model_promotion_ready",
                title="모델 승격 준비 완료",
                message=f"{model.version} 후보 모델의 승격 조건이 충족되었습니다.",
                created_at=model.created_at,
                model_id=model.id,
                model_task=model.task,
            )
        )

    items = sorted(
        [*risk_items, *model_items],
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    return NotificationListRead(
        items=items,
        total=len(items),
        risk_count=len(risk_items),
        model_promotion_count=len(model_items),
    )
