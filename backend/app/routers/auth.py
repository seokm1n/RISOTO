"""회원가입·로그인·로그아웃 및 현재 세션 조회 API를 제공한다."""

from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, create_auth_session, require_auth
from app.config import get_settings
from app.database import get_db
from app.models import Company, User
from app.schemas import (
    AuthLoginRequest,
    AuthMeRead,
    AuthPasswordChangeRequest,
    AuthSignupRequest,
    AuthUserRead,
)


router = APIRouter(prefix="/auth", tags=["auth"])
password_hasher = PasswordHasher()


def _set_session_cookies(response: Response, raw_token: str, raw_csrf_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure or settings.app_env.casefold() == "production",
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=raw_csrf_token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.session_cookie_secure or settings.app_env.casefold() == "production",
        samesite="lax",
        path="/",
    )


def _auth_read(db: Session, auth: CurrentAuth) -> AuthMeRead:
    has_main_company = db.scalar(
        select(Company.id).where(
            Company.user_id == auth.user_id,
            Company.company_role == "main",
        ).limit(1)
    ) is not None
    return AuthMeRead(
        user=AuthUserRead(
            id=auth.user.id,
            email=auth.user.email,
            role=auth.user.role,
        ),
        has_main_company=has_main_company,
        csrf_token=auth.csrf_token,
    )


@router.post("/signup", response_model=AuthMeRead, status_code=status.HTTP_201_CREATED)
def signup(
    payload: AuthSignupRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthMeRead:
    if payload.email == "admin@company.com":
        raise HTTPException(status_code=409, detail="관리자 계정은 회원가입으로 만들 수 없습니다.")
    if db.scalar(select(User.id).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    user = User(
        email=payload.email,
        password_hash=password_hasher.hash(payload.password),
        role="general",
    )
    db.add(user)
    try:
        db.flush()
        auth_session, raw_token, raw_csrf_token = create_auth_session(db, user.id)
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
        db.refresh(auth_session)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.") from exc

    _set_session_cookies(response, raw_token, raw_csrf_token)
    return _auth_read(
        db,
        CurrentAuth(
            user=user,
            session=auth_session,
            csrf_token=raw_csrf_token,
        ),
    )


@router.post("/login", response_model=AuthMeRead)
def login(
    payload: AuthLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthMeRead:
    user = db.scalar(select(User).where(User.email == payload.email))
    valid = False
    if user is not None and user.is_active:
        try:
            valid = password_hasher.verify(user.password_hash, payload.password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
    if not valid or user is None:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(payload.password)
    user.last_login_at = datetime.now(timezone.utc)
    auth_session, raw_token, raw_csrf_token = create_auth_session(db, user.id)
    db.commit()
    db.refresh(auth_session)
    _set_session_cookies(response, raw_token, raw_csrf_token)
    return _auth_read(
        db,
        CurrentAuth(
            user=user,
            session=auth_session,
            csrf_token=raw_csrf_token,
        ),
    )


@router.get("/me", response_model=AuthMeRead)
def me(
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> AuthMeRead:
    return _auth_read(db, auth)


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: AuthPasswordChangeRequest,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> Response:
    """별도 본인 인증 없이 로그인 세션에서 새 비밀번호를 저장한다."""
    auth.user.password_hash = password_hasher.hash(payload.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> Response:
    auth.session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure or settings.app_env.casefold() == "production",
        samesite="lax",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        httponly=False,
        secure=settings.session_cookie_secure or settings.app_env.casefold() == "production",
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
