"""DB 기반 불투명 세션과 요청 단위 인증·CSRF 검증을 제공한다."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuthSession, User


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True, slots=True)
class CurrentAuth:
    """현재 사용자와 세션을 함께 전달하는 인증 컨텍스트."""

    user: User
    session: AuthSession
    csrf_token: str

    @property
    def user_id(self) -> int:
        return self.user.id


def hash_session_token(token: str) -> str:
    """원문 세션 토큰이 DB에 남지 않도록 SHA-256으로 단방향 변환한다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_auth_session(db: Session, user_id: int) -> tuple[AuthSession, str, str]:
    """새 세션을 저장 대기 상태로 만들고 브라우저용 원문 토큰을 한 번 반환한다."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)
    raw_csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    auth_session = AuthSession(
        user_id=user_id,
        token_hash=hash_session_token(raw_token),
        csrf_token_hash=hash_session_token(raw_csrf_token),
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        last_seen_at=now,
    )
    db.add(auth_session)
    return auth_session, raw_token, raw_csrf_token


def require_auth(
    request: Request,
    db: Session = Depends(get_db),
) -> CurrentAuth:
    """세션 쿠키, 계정 상태와 변경 요청의 CSRF 토큰을 검증한다."""
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")

    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_session_token(raw_token))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 세션입니다.")

    auth_session, user = row
    now = datetime.now(timezone.utc)
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if auth_session.revoked_at is not None or expires_at <= now or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었습니다.")

    raw_csrf_token = request.cookies.get(settings.csrf_cookie_name, "")
    if not raw_csrf_token or not secrets.compare_digest(
        hash_session_token(raw_csrf_token), auth_session.csrf_token_hash
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션 보호 토큰이 없습니다.")

    if request.method.upper() not in SAFE_METHODS:
        supplied_csrf = request.headers.get("X-CSRF-Token", "")
        if not supplied_csrf or not secrets.compare_digest(supplied_csrf, raw_csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF 토큰이 없거나 올바르지 않습니다.",
            )

    auth_session.last_seen_at = now
    db.commit()
    return CurrentAuth(
        user=user,
        session=auth_session,
        csrf_token=raw_csrf_token,
    )
