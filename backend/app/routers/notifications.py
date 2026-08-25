"""Read-only unified notifications for risks and promotion-ready models."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.database import get_db
from app.models import Company, RiskEvent
from app.schemas import NotificationItemRead, NotificationListRead


router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationListRead)
def list_notifications(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> NotificationListRead:
    """Return risk notifications owned by the current workspace."""
    risk_rows = db.execute(
        select(RiskEvent, Company)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            Company.workspace_id == auth.workspace_id,
            RiskEvent.status.in_(("open", "monitoring")),
        )
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

    items = sorted(
        risk_items,
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    return NotificationListRead(
        items=items,
        total=len(items),
        risk_count=len(risk_items),
        model_promotion_count=0,
    )
