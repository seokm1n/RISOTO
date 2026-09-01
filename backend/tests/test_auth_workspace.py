"""Authentication, session-cookie, CSRF and onboarding contracts."""

from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import unittest
from uuid import uuid4

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.auth import CurrentAuth, create_auth_session, require_auth
from app.config import get_settings
from app.database import engine
from app.models import AuthSession, Company, User
from app.routers.auth import (
    change_password,
    delete_account,
    login,
    logout,
    me,
    password_hasher,
    signup,
)
from app.schemas import (
    AuthAccountDeleteRequest,
    AuthLoginRequest,
    AuthPasswordChangeRequest,
    AuthSignupRequest,
)


def _response_cookies(response: Response) -> dict[str, str]:
    values: dict[str, str] = {}
    for header in response.headers.getlist("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        values.update({name: morsel.value for name, morsel in jar.items()})
    return values


def _request(method: str, cookies: dict[str, str], csrf: str | None = None) -> Request:
    headers = [(b"cookie", "; ".join(f"{key}={value}" for key, value in cookies.items()).encode())]
    if csrf is not None:
        headers.append((b"x-csrf-token", csrf.encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1/test",
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )


class AuthDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def test_signup_login_csrf_expiry_and_logout(self):
        suffix = uuid4().hex
        normalized_email = f"auth-{suffix}@example.com"
        payload = AuthSignupRequest(
            email=f"  AUTH-{suffix}@EXAMPLE.COM  ",
            password="12345678",
        )
        self.assertEqual(payload.email, normalized_email)

        signup_response = Response()
        signed_up = signup(payload, signup_response, self.db)
        cookies = _response_cookies(signup_response)
        settings = get_settings()

        self.assertEqual(signed_up.user.email, normalized_email)
        self.assertFalse(signed_up.has_main_company)
        self.assertNotIn("workspace", signed_up.model_dump())
        self.assertIn(settings.session_cookie_name, cookies)
        self.assertIn(settings.csrf_cookie_name, cookies)
        session_cookie_header = next(
            value for value in signup_response.headers.getlist("set-cookie")
            if value.startswith(f"{settings.session_cookie_name}=")
        )
        self.assertIn("HttpOnly", session_cookie_header)
        self.assertIn("SameSite=lax", session_cookie_header)

        user = self.db.scalar(select(User).where(User.email == normalized_email))
        self.assertIsNotNone(user)
        self.assertTrue(user.password_hash.startswith("$argon2id$"))
        self.assertTrue(password_hasher.verify(user.password_hash, "12345678"))
        signup_session = self.db.scalar(
            select(AuthSession).where(AuthSession.user_id == user.id)
        )
        self.assertIsNotNone(signup_session)

        auth = require_auth(_request("GET", cookies), self.db)
        self.assertEqual(auth.user_id, user.id)
        self.assertFalse(me(self.db, auth).has_main_company)

        with self.assertRaises(HTTPException) as missing_csrf:
            require_auth(_request("POST", cookies), self.db)
        self.assertEqual(missing_csrf.exception.status_code, 403)
        unsafe_auth = require_auth(
            _request("POST", cookies, cookies[settings.csrf_cookie_name]),
            self.db,
        )
        self.assertEqual(unsafe_auth.user_id, user.id)

        with self.assertRaises(HTTPException) as wrong_password:
            login(
                AuthLoginRequest(email=normalized_email, password="87654321"),
                Response(),
                self.db,
            )
        self.assertEqual(wrong_password.exception.status_code, 401)

        login_response = Response()
        logged_in = login(
            AuthLoginRequest(email=normalized_email.upper(), password="12345678"),
            login_response,
            self.db,
        )
        self.assertEqual(logged_in.user.email, normalized_email)
        login_cookies = _response_cookies(login_response)
        login_auth = require_auth(_request("GET", login_cookies), self.db)

        logout_response = Response()
        logout(logout_response, self.db, login_auth)
        self.assertIsNotNone(login_auth.session.revoked_at)
        with self.assertRaises(HTTPException) as revoked:
            require_auth(_request("GET", login_cookies), self.db)
        self.assertEqual(revoked.exception.status_code, 401)

        signup_session = self.db.scalar(
            select(AuthSession).where(AuthSession.id == auth.session.id)
        )
        signup_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()
        with self.assertRaises(HTTPException) as expired:
            require_auth(_request("GET", cookies), self.db)
        self.assertEqual(expired.exception.status_code, 401)

    def test_password_and_email_validation(self):
        with self.assertRaises(ValidationError):
            AuthSignupRequest(email="not-an-email", password="12345678")
        with self.assertRaises(ValidationError):
            AuthSignupRequest(email="short@example.com", password="1234567")
        with self.assertRaises(ValidationError):
            AuthPasswordChangeRequest(
                new_password="new-password",
                new_password_confirmation="different-password",
            )

    def test_change_password_without_current_password(self):
        suffix = uuid4().hex
        user = User(
            email=f"password-{suffix}@example.com",
            password_hash=password_hasher.hash("old-password"),
        )
        self.db.add(user)
        self.db.flush()
        auth_session, _, csrf_token = create_auth_session(self.db, user.id)
        self.db.commit()

        response = change_password(
            AuthPasswordChangeRequest(
                new_password="new-password",
                new_password_confirmation="new-password",
            ),
            self.db,
            CurrentAuth(user=user, session=auth_session, csrf_token=csrf_token),
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(password_hasher.verify(user.password_hash, "new-password"))

    def test_delete_account_requires_password_and_removes_owned_data(self):
        suffix = uuid4().hex
        user = User(
            email=f"delete-{suffix}@example.com",
            password_hash=password_hasher.hash("current-password"),
            role="general",
        )
        self.db.add(user)
        self.db.flush()
        company = Company(
            user_id=user.id,
            name=f"탈퇴 테스트 {suffix}",
            normalized_name=f"탈퇴 테스트 {suffix}",
            company_role="main",
            annual_revenue_krw=10_000_000_000,
            company_size_class="large",
            backfill_days=7,
            monitoring_status="active",
            analysis_status="pending",
        )
        self.db.add(company)
        auth_session, _, csrf_token = create_auth_session(self.db, user.id)
        self.db.commit()
        user_id = user.id
        company_id = company.id
        session_id = auth_session.id
        auth = CurrentAuth(user=user, session=auth_session, csrf_token=csrf_token)

        with self.assertRaises(HTTPException) as wrong_password:
            delete_account(
                AuthAccountDeleteRequest(current_password="wrong-password"),
                Response(),
                self.db,
                auth,
            )
        self.assertEqual(wrong_password.exception.status_code, 400)
        self.assertIsNotNone(self.db.scalar(select(User.id).where(User.id == user_id)))

        response = Response()
        deleted = delete_account(
            AuthAccountDeleteRequest(current_password="current-password"),
            response,
            self.db,
            auth,
        )

        self.assertEqual(deleted.status_code, 204)
        self.assertIsNone(self.db.scalar(select(User.id).where(User.id == user_id)))
        self.assertIsNone(self.db.scalar(select(Company.id).where(Company.id == company_id)))
        self.assertIsNone(
            self.db.scalar(select(AuthSession.id).where(AuthSession.id == session_id))
        )
        cookies = _response_cookies(response)
        settings = get_settings()
        self.assertIn(settings.session_cookie_name, cookies)
        self.assertIn(settings.csrf_cookie_name, cookies)

    def test_delete_account_rejects_admin(self):
        suffix = uuid4().hex
        user = User(
            email=f"admin-delete-{suffix}@example.com",
            password_hash=password_hasher.hash("current-password"),
            role="admin",
        )
        self.db.add(user)
        self.db.flush()
        auth_session, _, csrf_token = create_auth_session(self.db, user.id)
        self.db.commit()

        with self.assertRaises(HTTPException) as forbidden:
            delete_account(
                AuthAccountDeleteRequest(current_password="current-password"),
                Response(),
                self.db,
                CurrentAuth(user=user, session=auth_session, csrf_token=csrf_token),
            )
        self.assertEqual(forbidden.exception.status_code, 403)
        self.assertIsNotNone(self.db.scalar(select(User.id).where(User.id == user.id)))


if __name__ == "__main__":
    unittest.main()
