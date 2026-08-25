"""Small authenticated-context builders for direct router unit tests."""

from sqlalchemy import select

from app.auth import CurrentAuth
from app.models import AuthSession, Company, User, Workspace, WorkspaceMember


def auth_for_company(db, company_id: int) -> CurrentAuth:
    workspace_id = db.scalar(
        select(Company.workspace_id).where(Company.id == company_id)
    )
    if workspace_id is None:
        raise AssertionError("test company has no workspace")
    return auth_for_workspace(db, workspace_id)


def auth_for_workspace(db, workspace_id: int) -> CurrentAuth:
    workspace = db.get(Workspace, workspace_id)
    user = db.scalar(
        select(User)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(User.id)
        .limit(1)
    )
    if workspace is None or user is None:
        raise AssertionError("test workspace has no member")
    # Direct router tests do not exercise cookie/session validation; FastAPI's
    # require_auth dependency owns that contract. A transient session is enough
    # to satisfy the typed request context used by the route implementation.
    session = AuthSession(user_id=user.id, workspace_id=workspace.id)
    return CurrentAuth(
        user=user,
        workspace=workspace,
        session=session,
        csrf_token="test-csrf",
    )
