"""관리자 전용 회원·전체 수집 관리 API."""

from datetime import date, datetime, timedelta, timezone

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Date as SqlDate
from sqlalchemy import cast, func, select, update
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_admin
from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    CollectionIncident,
    Company,
    CompanyArticleMatch,
    NewsArticle,
    RiskEvent,
    StoryClusterArticle,
    User,
    AuthSession,
)
from app.routers.collection import provider_status
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES
from app.schemas import (
    AdminCollectionCompanyRead,
    AdminCollectionDailyRead,
    AdminCollectionOverviewRead,
    AdminCollectionProviderRead,
    AdminMemberPage,
    AdminMemberRead,
    AdminPasswordResetRequest,
    BulkMonitoringStateResponse,
    CollectionIncidentRead,
)


router = APIRouter(prefix="/admin", tags=["admin"])
password_hasher = PasswordHasher()


@router.get("/members", response_model=AdminMemberPage)
def list_members(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> AdminMemberPage:
    """관리자 화면에는 일반 회원과 해당 회원의 기업 요약만 표시한다."""
    base_query = select(User).where(User.role == "general")
    total = int(db.scalar(select(func.count()).select_from(base_query.subquery())) or 0)
    users = list(
        db.scalars(
            base_query.order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items: list[AdminMemberRead] = []
    for user in users:
        companies = list(
            db.scalars(
                select(Company)
                .where(Company.user_id == user.id)
                .order_by(Company.company_role, Company.created_at.desc())
            )
        )
        main = next((company for company in companies if company.company_role == "main"), None)
        competitors = [company.name for company in companies if company.company_role == "competitor"]
        items.append(
            AdminMemberRead(
                id=user.id,
                email=user.email,
                role=user.role,
                is_active=user.is_active,
                created_at=user.created_at,
                last_login_at=user.last_login_at,
                main_company_name=main.name if main else None,
                competitor_count=len(competitors),
                competitor_names=competitors,
            )
        )
    return AdminMemberPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/members/{user_id}/password-reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_member_password(
    user_id: int,
    payload: AdminPasswordResetRequest,
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
) -> Response:
    """일반 회원의 비밀번호를 재설정하고 기존 세션을 모두 폐기한다."""
    user = db.get(User, user_id)
    if user is None or user.role != "general":
        raise HTTPException(status_code=404, detail="일반 회원을 찾을 수 없습니다.")
    user.password_hash = password_hasher.hash(payload.new_password)
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _collection_daily_counts(db: Session, cutoff: date) -> list[AdminCollectionDailyRead]:
    article_day = cast(
        func.coalesce(NewsArticle.published_at, NewsArticle.created_at),
        SqlDate,
    )
    collected_rows = db.execute(
        select(article_day.label("day"), func.count(CompanyArticleMatch.article_id))
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .where(article_day >= cutoff)
        .group_by(article_day)
    ).all()
    story_rows = db.execute(
        select(
            article_day.label("day"),
            func.count(func.distinct(StoryClusterArticle.story_cluster_id)),
        )
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
        .where(article_day >= cutoff)
        .group_by(article_day)
    ).all()
    risk_day = cast(RiskEvent.detected_at, SqlDate)
    risk_rows = db.execute(
        select(risk_day.label("day"), func.count(RiskEvent.id))
        .where(
            risk_day >= cutoff,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        )
        .group_by(risk_day)
    ).all()
    collected = {row.day: int(row[1] or 0) for row in collected_rows}
    stories = {row.day: int(row[1] or 0) for row in story_rows}
    risks = {row.day: int(row[1] or 0) for row in risk_rows}
    days = sorted(set(collected) | set(risks))
    return [
        AdminCollectionDailyRead(
            day=day,
            collected_count=collected.get(day, 0),
            story_count=stories.get(day, 0),
            risk_count=risks.get(day, 0),
        )
        for day in days
    ]


@router.get("/collection/overview", response_model=AdminCollectionOverviewRead)
def collection_overview(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
    _auth: CurrentAuth = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> AdminCollectionOverviewRead:
    """모든 일반 회원의 기업·수집량·위험량을 관리자용으로 집계한다."""
    companies_query = (
        select(
            Company,
            User.email,
            func.count(func.distinct(CompanyArticleMatch.article_id)).label("article_count"),
            func.count(func.distinct(RiskEvent.id)).label("risk_count"),
        )
        .join(User, User.id == Company.user_id)
        .outerjoin(CompanyArticleMatch, CompanyArticleMatch.company_id == Company.id)
        .outerjoin(RiskEvent, RiskEvent.company_id == Company.id)
        .group_by(Company.id, User.email)
        .order_by(Company.created_at.desc(), Company.id.desc())
        .limit(500)
    )
    company_rows = db.execute(companies_query).all()
    company_items = [
        AdminCollectionCompanyRead(
            id=company.id,
            name=company.name,
            owner_email=email,
            company_role=company.company_role,
            monitoring_status=company.monitoring_status,
            article_count=int(article_count or 0),
            risk_count=int(risk_count or 0),
            last_collected_at=company.last_collected_at,
        )
        for company, email, article_count, risk_count in company_rows
    ]
    total_companies = len(company_items)
    active_companies = sum(
        item.monitoring_status in {"backfilling", "warming", "active"}
        for item in company_items
    )
    collected_count = int(db.scalar(select(func.count()).select_from(CompanyArticleMatch)) or 0)
    risk_count = int(
        db.scalar(
            select(func.count()).select_from(RiskEvent).where(RiskEvent.status != "legacy_candidate")
        )
        or 0
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
    incidents = list(
        db.scalars(
            select(CollectionIncident)
            .order_by(CollectionIncident.detected_at.desc())
            .limit(20)
        )
    )
    provider_flags = provider_status(settings)
    providers = [
        AdminCollectionProviderRead(source=source, status="연결됨" if connected else "미설정")
        for source, connected in (
            ("네이버 뉴스", provider_flags.naver),
            ("Tavily 뉴스", provider_flags.tavily),
            ("Daum 검색", provider_flags.kakao),
            ("YouTube 댓글", provider_flags.youtube),
        )
    ]
    return AdminCollectionOverviewRead(
        days=days,
        total_companies=total_companies,
        active_companies=active_companies,
        collected_count=collected_count,
        risk_count=risk_count,
        daily=_collection_daily_counts(db, cutoff),
        companies=company_items,
        providers=providers,
        incidents=[CollectionIncidentRead.model_validate(item) for item in incidents],
    )


@router.post("/collection/monitoring/{action}", response_model=BulkMonitoringStateResponse)
def set_all_monitoring_state(
    action: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _auth: CurrentAuth = Depends(require_admin),
) -> BulkMonitoringStateResponse:
    """관리자가 전체 회원의 실시간 수집을 정지하거나 재개한다."""
    if action not in {"pause", "resume"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 전체 수집 작업입니다.")
    target_statuses = {"backfilling", "warming", "active"} if action == "pause" else {"paused"}
    next_status = "paused" if action == "pause" else "active"
    values = {"monitoring_status": next_status}
    if action == "resume":
        values["next_collection_at"] = datetime.now(timezone.utc) + timedelta(
            seconds=settings.realtime_interval_seconds
        )
    result = db.execute(
        update(Company)
        .where(Company.monitoring_status.in_(target_statuses))
        .values(**values)
    )
    total = int(db.scalar(select(func.count()).select_from(Company)) or 0)
    db.commit()
    return BulkMonitoringStateResponse(
        action=action,
        monitoring_status=next_status,
        updated_count=int(result.rowcount or 0),
        total_count=total,
    )
