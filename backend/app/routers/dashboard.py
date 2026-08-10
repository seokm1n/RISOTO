from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Company, CompanyArticleMatch, NewsArticle, RiskEvent
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
) -> DashboardOverview:
    """Return compact, dashboard-ready monitoring statistics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)

    total_companies = db.scalar(
        select(func.count()).select_from(Company).where(Company.monitoring_status != "archived")
    ) or 0
    active_companies = db.scalar(
        select(func.count()).select_from(Company).where(Company.monitoring_status == "active")
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
        .where(article_time >= cutoff)
    ).mappings().one()
    risk_count = db.scalar(
        select(func.count()).select_from(RiskEvent).where(RiskEvent.detected_at >= cutoff)
    ) or 0

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
        .where(article_time >= cutoff)
        .group_by(day)
        .order_by(day)
    ).mappings().all()
    risk_by_day = dict(
        db.execute(
            select(
                func.date_trunc("day", RiskEvent.detected_at).label("day"),
                func.count(RiskEvent.id).label("risk_count"),
            )
            .where(RiskEvent.detected_at >= cutoff)
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

    sentiment_label = case(
        (NewsArticle.sentiment_label.in_(["positive", "긍정"]), "positive"),
        (NewsArticle.sentiment_label.in_(["negative", "부정"]), "negative"),
        else_=NewsArticle.sentiment_label,
    ).label("label")
    sentiment_rows = db.execute(
        select(sentiment_label, func.count(CompanyArticleMatch.article_id))
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .where(article_time >= cutoff, NewsArticle.sentiment_label.is_not(None))
        .group_by(sentiment_label)
    ).all()
    sentiments = [DashboardSentimentRead(label=label, count=count) for label, count in sentiment_rows]

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
        .where(RiskEvent.company_id == Company.id, RiskEvent.detected_at >= cutoff)
        .correlate(Company)
        .scalar_subquery()
    )
    company_rows = db.execute(
        select(
            Company.id,
            Company.name,
            Company.monitoring_status,
            company_article_count.label("article_count"),
            company_negative_count.label("negative_count"),
            company_risk_count.label("risk_count"),
        )
        .select_from(Company)
        .where(Company.monitoring_status != "archived")
        .order_by(company_article_count.desc(), Company.name)
    ).mappings().all()
    companies = [DashboardCompanyRead(**row) for row in company_rows]

    risk_rows = db.execute(
        select(RiskEvent, NewsArticle)
        .join(NewsArticle, NewsArticle.id == RiskEvent.article_id)
        .where(RiskEvent.detected_at >= cutoff)
        .order_by(RiskEvent.detected_at.desc())
        .limit(10)
    ).all()
    recent_risks = [
        RiskEventRead(
            id=event.id,
            company_id=event.company_id,
            article_id=event.article_id,
            article_title=article.title,
            article_url=article.url,
            anomaly_score=event.anomaly_score,
            severity=event.severity,
            status=event.status,
            detected_at=event.detected_at,
        )
        for event, article in risk_rows
    ]

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
    )
