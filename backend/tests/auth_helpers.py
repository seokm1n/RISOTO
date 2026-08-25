"""Small authenticated-context builders for direct router unit tests."""

from sqlalchemy import select

from app.auth import CurrentAuth
from app.models import AuthSession, Company, User


def auth_for_company(db, company_id: int) -> CurrentAuth:
    user_id = db.scalar(
        select(Company.user_id).where(Company.id == company_id)
    )
    if user_id is None:
        raise AssertionError("test company has no owner")
    return auth_for_user(db, user_id)


def auth_for_user(db, user_id: int) -> CurrentAuth:
    user = db.get(User, user_id)
    if user is None:
        raise AssertionError("test user does not exist")
    # Direct router tests do not exercise cookie/session validation; FastAPI's
    # require_auth dependency owns that contract. A transient session is enough
    # to satisfy the typed request context used by the route implementation.
    session = AuthSession(user_id=user.id)
    return CurrentAuth(
        user=user,
        session=session,
        csrf_token="test-csrf",
    )
