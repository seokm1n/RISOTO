from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    CollectionJob,
    Company,
    CompanyArticleMatch,
    CompanyBaseline,
    NewsArticle,
    RiskEvent,
)
from app.schemas import (
    CollectionJobRead,
    CollectionJobPage,
    CollectionProviderStatus,
    CollectionRequest,
    MonitoringSummary,
    NewsArticleRead,
    NewsArticlePage,
    RiskEventRead,
)
from app.services.monitoring_pipeline import run_collection


router = APIRouter(tags=["collection"])


def provider_status(settings: Settings) -> CollectionProviderStatus:
    return CollectionProviderStatus(
        naver=bool(settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret),
        tavily=bool(settings.tavily_api_key),
        serpapi=bool(settings.serpapi_api_key),
        youtube=bool(settings.youtube_api_key),
    )


@router.get("/collection/providers", response_model=CollectionProviderStatus)
def get_provider_status(settings: Settings = Depends(get_settings)) -> CollectionProviderStatus:
    return provider_status(settings)


@router.post("/companies/{company_id}/collect", response_model=CollectionJobRead)
def collect_company_news(
    company_id: int,
    payload: CollectionRequest,
    db: Session = Depends(get_db),
) -> CollectionJob:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    return run_collection(
        company_id,
        "manual",
        datetime.now(timezone.utc) - timedelta(days=company.backfill_days),
        sources=payload.sources,
        max_queries=payload.max_queries,
    )


@router.get("/companies/{company_id}/collection-jobs", response_model=CollectionJobPage)
def list_collection_jobs(
    company_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CollectionJobPage:
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    query = select(CollectionJob).where(CollectionJob.company_id == company_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(db.scalars(
        query.order_by(CollectionJob.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ))
    return CollectionJobPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/companies/{company_id}/monitoring/{action}", response_model=MonitoringSummary)
def set_monitoring_state(
    company_id: int,
    action: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MonitoringSummary:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    next_collection_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.realtime_interval_seconds
    )
    if action == "pause":
        company.monitoring_status = "paused"
        company.next_collection_at = next_collection_at
    elif action == "resume":
        company.monitoring_status = "warming"
        company.next_collection_at = next_collection_at
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 모니터링 작업입니다.")
    db.commit()
    return get_monitoring_summary(company_id, db, settings)


@router.get("/companies/{company_id}/articles", response_model=NewsArticlePage)
def list_company_articles(
    company_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    source: str | None = Query(default=None, min_length=1, max_length=40),
    db: Session = Depends(get_db),
) -> NewsArticlePage:
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    company_articles = (
        select(NewsArticle, CompanyArticleMatch)
        .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
        .where(CompanyArticleMatch.company_id == company_id)
    )
    sources = list(db.scalars(
        select(NewsArticle.source)
        .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
        .where(CompanyArticleMatch.company_id == company_id)
        .distinct()
        .order_by(NewsArticle.source)
    ))
    base_query = company_articles.where(NewsArticle.source == source) if source else company_articles
    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = db.execute(
        base_query
        .order_by(NewsArticle.published_at.desc().nullslast(), NewsArticle.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        NewsArticleRead(
            id=article.id,
            source=article.source,
            title=article.title,
            summary=article.summary,
            url=article.url,
            original_url=article.original_url,
            published_at=article.published_at,
            matched_keyword=match.matched_keyword,
            sentiment_label=article.sentiment_label,
            sentiment_score=article.sentiment_score,
            sentiment_confidence=article.sentiment_confidence,
            anomaly_score=match.anomaly_score,
            is_anomaly=match.is_anomaly,
            created_at=article.created_at,
        )
        for article, match in rows
    ]
    return NewsArticlePage(
        items=items, total=total, page=page, page_size=page_size, sources=sources
    )


@router.get("/companies/{company_id}/monitoring", response_model=MonitoringSummary)
def get_monitoring_summary(
    company_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MonitoringSummary:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    article_count = db.scalar(
        select(func.count()).select_from(CompanyArticleMatch).where(
            CompanyArticleMatch.company_id == company_id
        )
    ) or 0
    analyzed_count = db.scalar(
        select(func.count())
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .where(
            CompanyArticleMatch.company_id == company_id,
            NewsArticle.analyzed_at.is_not(None),
        )
    ) or 0
    anomaly_count = db.scalar(
        select(func.count()).select_from(CompanyArticleMatch).where(
            CompanyArticleMatch.company_id == company_id,
            CompanyArticleMatch.is_anomaly.is_(True),
        )
    ) or 0
    baseline = db.get(CompanyBaseline, company_id)
    return MonitoringSummary(
        company_id=company_id,
        monitoring_status=company.monitoring_status,
        analysis_status=company.analysis_status,
        article_count=article_count,
        analyzed_count=analyzed_count,
        anomaly_count=anomaly_count,
        last_collected_at=company.last_collected_at,
        baseline_ready_at=company.baseline_ready_at,
        baseline_training_articles=baseline.training_article_count if baseline else None,
        baseline_training_days=baseline.training_day_count if baseline else None,
        collection_interval_seconds=settings.realtime_interval_seconds,
        next_collection_at=company.next_collection_at,
    )


@router.get("/companies/{company_id}/risk-events", response_model=list[RiskEventRead])
def list_risk_events(
    company_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[RiskEventRead]:
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    rows = db.execute(
        select(RiskEvent, NewsArticle)
        .join(NewsArticle, NewsArticle.id == RiskEvent.article_id)
        .where(RiskEvent.company_id == company_id)
        .order_by(RiskEvent.detected_at.desc())
        .limit(limit)
    ).all()
    return [
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
        for event, article in rows
    ]
