"""현재 워크스페이스 설정 조회·수정 API를 제공한다."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.database import get_db
from app.schemas import WorkspaceRead, WorkspaceUpdate


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _workspace_read(auth: CurrentAuth) -> WorkspaceRead:
    return WorkspaceRead(
        id=auth.workspace.id,
        name=auth.workspace.name,
        competitor_company_label=auth.workspace.competitor_company_label,
    )


@router.get("/current", response_model=WorkspaceRead)
def get_current_workspace(auth: CurrentAuth = Depends(require_auth)) -> WorkspaceRead:
    return _workspace_read(auth)


@router.patch("/current", response_model=WorkspaceRead)
def update_current_workspace(
    payload: WorkspaceUpdate,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> WorkspaceRead:
    auth.workspace.competitor_company_label = payload.competitor_company_label
    db.commit()
    db.refresh(auth.workspace)
    return _workspace_read(auth)
