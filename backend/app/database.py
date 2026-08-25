"""SQLAlchemy 엔진과 요청 단위 세션 생명주기를 구성하는 모듈."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """모든 RISOTO ORM 모델이 상속하는 SQLAlchemy 선언형 기반 클래스."""

    pass


settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """요청 단위 데이터베이스 세션을 제공하고 사용 후 반드시 닫는다."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
