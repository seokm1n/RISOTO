"""기업·기사·감성·위험 데이터를 기간별 대시보드 통계로 집계한다."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.database import get_db
from app.models import (
    CollectionIncident,
    Company,
    CompanyArticleMatch,
    CompanyFeatureWindow,
    NewsArticle,
    RiskEvent,
    StoryClusterArticle,
)
from app.presenters import risk_event_read
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES
from app.schemas import (
    DashboardCompanyRead,
    DashboardDailyRead,
    DashboardOverview,
    DashboardSentimentRead,
    RiskEventRead,
)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverview)
def get_dashboard_overview(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> DashboardOverview:
    """선택 기간의 기업·기사·감성·위험 현황을 대시보드용 통계로 집계한다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # 발행일이 없는 기사도 누락되지 않도록 저장 시각을 통계 기준 시각으로 대체한다.
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)

    total_companies = db.scalar(
        select(func.count()).select_from(Company).where(
            Company.user_id == auth.user_id,
            Company.monitoring_status != "archived",
        )
    ) or 0
    active_companies = db.scalar(
        select(func.count()).select_from(Company).where(
            Company.user_id == auth.user_id,
            Company.monitoring_status == "active",
        )
    ) or 0

    totals = db.execute(
        select(
            func.count(CompanyArticleMatch.article_id).label("article_count"),
            func.count(CompanyArticleMatch.article_id)
            .filter(NewsArticle.analyzed_at.is_not(None))
            .label("analyzed_count"),
            func.count(CompanyArticleMatch.article_id)
            .filter(NewsArticle.sentiment_label.in_(["negative", "부정"]))
            .label("negative_count"),
        )
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .join(Company, Company.id == CompanyArticleMatch.company_id)
        .where(Company.user_id == auth.user_id, article_time >= cutoff)
    ).mappings().one()
    risk_count = db.scalar(
        select(func.count()).select_from(RiskEvent).join(
            Company, Company.id == RiskEvent.company_id
        ).where(
            Company.user_id == auth.user_id,
            RiskEvent.detected_at >= cutoff,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        )
    ) or 0

    # 기사 추세와 위험 추세를 별도로 집계한 뒤 동일한 날짜 키로 결합한다.
    day = func.date_trunc("day", article_time).label("day")
    daily_rows = db.execute(
        select(
            day,
            func.count(CompanyArticleMatch.article_id).label("article_count"),
            func.count(CompanyArticleMatch.article_id)
            .filter(NewsArticle.sentiment_label.in_(["positive", "긍정"]))
            .label("positive_count"),
            func.count(CompanyArticleMatch.article_id)
            .filter(NewsArticle.sentiment_label.in_(["negative", "부정"]))
            .label("negative_count"),
        )
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .join(Company, Company.id == CompanyArticleMatch.company_id)
        .where(Company.user_id == auth.user_id, article_time >= cutoff)
        .group_by(day)
        .order_by(day)
    ).mappings().all()
    risk_by_day = dict(
        db.execute(
            select(
                func.date_trunc("day", RiskEvent.detected_at).label("day"),
                func.count(RiskEvent.id).label("risk_count"),
            )
            .join(Company, Company.id == RiskEvent.company_id)
            .where(
                Company.user_id == auth.user_id,
                RiskEvent.detected_at >= cutoff,
                RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
            )
            .group_by("day")
        ).all()
    )
    daily = [
        DashboardDailyRead(
            day=row["day"],
            article_count=row["article_count"],
            positive_count=row["positive_count"],
            negative_count=row["negative_count"],
            risk_count=risk_by_day.get(row["day"], 0),
        )
        for row in daily_rows
    ]

    # 과거 한글 레이블과 현재 영문 레이블을 같은 대시보드 범주로 통합한다.
    sentiment_label = case(
        (NewsArticle.sentiment_label.in_(["positive", "긍정"]), "positive"),
        (NewsArticle.sentiment_label.in_(["negative", "부정"]), "negative"),
        else_=NewsArticle.sentiment_label,
    ).label("label")
    sentiment_rows = db.execute(
        select(sentiment_label, func.count(CompanyArticleMatch.article_id))
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .join(Company, Company.id == CompanyArticleMatch.company_id)
        .where(
            Company.user_id == auth.user_id,
            article_time >= cutoff,
            NewsArticle.sentiment_label.is_not(None),
        )
        .group_by(sentiment_label)
    ).all()
    sentiments = [DashboardSentimentRead(label=label, count=count) for label, count in sentiment_rows]

    # 상관 서브쿼리로 기업별 세 지표를 한 번의 기업 목록 조회에 포함한다.
    company_article_count = (
        select(func.count(CompanyArticleMatch.article_id))
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .where(CompanyArticleMatch.company_id == Company.id, article_time >= cutoff)
        .correlate(Company)
        .scalar_subquery()
    )
    company_negative_count = (
        select(func.count(CompanyArticleMatch.article_id))
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .where(
            CompanyArticleMatch.company_id == Company.id,
            article_time >= cutoff,
            NewsArticle.sentiment_label.in_(["negative", "부정"]),
        )
        .correlate(Company)
        .scalar_subquery()
    )
    company_risk_count = (
        select(func.count(RiskEvent.id))
        .where(
            RiskEvent.company_id == Company.id,
            RiskEvent.detected_at >= cutoff,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        )
        .correlate(Company)
        .scalar_subquery()
    )
    company_rows = db.execute(
        select(
            Company.id,
            Company.name,
            Company.company_role,
            (
                cast(Company.annual_revenue_krw, Numeric(30, 2))
                / Decimal(100_000_000)
            ).label("annual_revenue_100m_krw"),
            Company.company_size_class,
            Company.monitoring_status,
            company_article_count.label("article_count"),
            company_negative_count.label("negative_count"),
            company_risk_count.label("risk_count"),
        )
        .select_from(Company)
        .where(
            Company.user_id == auth.user_id,
            Company.monitoring_status != "archived",
        )
        .order_by(company_article_count.desc(), Company.name)
    ).mappings().all()
    companies = [DashboardCompanyRead(**row) for row in company_rows]

    risk_rows = list(db.scalars(
        select(RiskEvent)
        .join(Company, Company.id == RiskEvent.company_id)
        .where(
            Company.user_id == auth.user_id,
            RiskEvent.detected_at >= cutoff,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        )
        .order_by(RiskEvent.detected_at.desc())
        .limit(10)
    ))
    recent_risks = [risk_event_read(db, event) for event in risk_rows]
    recent_incidents = list(
        db.scalars(
            select(CollectionIncident)
            .where(CollectionIncident.user_id == auth.user_id)
            .order_by(CollectionIncident.detected_at.desc())
            .limit(10)
        )
    )

    return DashboardOverview(
        days=days,
        total_companies=total_companies,
        active_companies=active_companies,
        article_count=totals["article_count"],
        analyzed_count=totals["analyzed_count"],
        negative_count=totals["negative_count"],
        risk_count=risk_count,
        daily=daily,
        sentiments=sentiments,
        companies=companies,
        recent_risks=recent_risks,
        recent_incidents=recent_incidents,
    )
