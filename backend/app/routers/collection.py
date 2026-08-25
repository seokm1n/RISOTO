"""수집 실행, 기사 조회, 필터 감사 및 모니터링 제어 API를 제공한다."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    ArticleQueryHit,
    ArticleFilterResult,
    CollectionJob,
    Company,
    CompanyArticleMatch,
    CompanyBaseline,
    CompanyFeatureWindow,
    NewsArticle,
    RawNewsArticle,
    RiskEvent,
    StoryClusterArticle,
)
from app.presenters import risk_event_read
from app.schemas import (
    ArticleFilterResultPage,
    ArticleFilterResultRead,
    ArticleFilterSummary,
    BulkMonitoringStateResponse,
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


def _resumed_monitoring_status(company: Company) -> str:
    """Keep human activation when resuming; unapproved companies remain in warm-up."""
    return "active" if company.analysis_status == "ready" else "warming"


def _latest_filter_results(company_id: int):
    """원문 기사별 가장 최근 필터 판정만 조회하는 쿼리를 만든다."""
    latest_ids = (
        select(func.max(ArticleFilterResult.id).label("id"))
        .where(ArticleFilterResult.company_id == company_id)
        .group_by(ArticleFilterResult.raw_article_id)
        .subquery()
    )
    return (
        select(ArticleFilterResult)
        .join(latest_ids, latest_ids.c.id == ArticleFilterResult.id)
    )


def provider_status(settings: Settings) -> CollectionProviderStatus:
    """API 자격 증명 설정을 바탕으로 제공자별 구성 여부를 계산한다."""
    return CollectionProviderStatus(
        naver=bool(settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret),
        tavily=bool(settings.tavily_api_key),
        kakao=bool(settings.kakao_rest_api_key),
        serpapi=bool(settings.serpapi_api_key),
        youtube=bool(settings.youtube_api_key),
    )


@router.get("/collection/providers", response_model=CollectionProviderStatus)
def get_provider_status(settings: Settings = Depends(get_settings)) -> CollectionProviderStatus:
    """현재 뉴스 수집 제공자의 활성화 상태를 반환한다."""
    return provider_status(settings)


@router.post("/companies/{company_id}/collect", response_model=CollectionJobRead)
def collect_company_news(
    company_id: int,
    payload: CollectionRequest,
    db: Session = Depends(get_db),
) -> CollectionJob:
    """지정 기업에 대해 사용자가 요청한 기간·소스로 수동 뉴스 수집을 실행한다."""
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
    """기업의 뉴스 수집 작업 이력을 최신순으로 페이지네이션해 반환한다."""
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    query = select(CollectionJob).where(CollectionJob.company_id == company_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(db.scalars(
        query.order_by(CollectionJob.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ))
    return CollectionJobPage(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/companies/{company_id}/filter-summary",
    response_model=ArticleFilterSummary,
)
def get_filter_summary(
    company_id: int,
    db: Session = Depends(get_db),
) -> ArticleFilterSummary:
    """기업별 최신 기사 필터 판정을 사유와 처리 방식별로 집계한다."""
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    latest = _latest_filter_results(company_id).subquery()

    def count_where(*conditions) -> int:
        """최신 판정 쿼리에 조건을 적용해 일치하는 행 수를 센다."""
        return db.scalar(
            select(func.count()).select_from(latest).where(*conditions)
        ) or 0

    return ArticleFilterSummary(
        company_id=company_id,
        raw_count=count_where(),
        accepted_count=count_where(latest.c.decision == "accepted"),
        rejected_count=count_where(latest.c.decision == "rejected"),
        duplicate_count=count_where(latest.c.reason == "duplicate"),
        advertisement_count=count_where(latest.c.reason == "advertisement"),
        irrelevant_count=count_where(latest.c.reason == "irrelevant"),
        review_required_count=count_where(latest.c.decision == "review_required"),
        ai_assisted_count=count_where(latest.c.classifier_kind.like("%ai%")),
        rules_only_count=count_where(latest.c.classifier_kind == "rules_only"),
        last_filtered_at=db.scalar(select(func.max(latest.c.filtered_at))),
    )


@router.get(
    "/companies/{company_id}/filter-results",
    response_model=ArticleFilterResultPage,
)
def list_filter_results(
    company_id: int,
    decision: str | None = Query(default=None, pattern="^(accepted|rejected|review_required)$"),
    reason: str | None = Query(default=None, pattern="^(accepted|duplicate|advertisement|irrelevant)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ArticleFilterResultPage:
    """기업의 최신 기사 필터 결과를 선택 조건과 페이지 단위로 조회한다."""
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    base = (
        _latest_filter_results(company_id)
        .join(RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id)
        .add_columns(RawNewsArticle)
    )
    if decision:
        base = base.where(ArticleFilterResult.decision == decision)
    if reason:
        base = base.where(ArticleFilterResult.reason == reason)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(ArticleFilterResult.filtered_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ArticleFilterResultPage(
        items=[
            ArticleFilterResultRead(
                id=result.id,
                raw_article_id=result.raw_article_id,
                curated_article_id=result.curated_article_id,
                source=raw.source,
                title=raw.title,
                url=raw.url,
                decision=result.decision,
                reason=result.reason,
                relevance_score=result.relevance_score,
                advertising_score=result.advertising_score,
                confidence=result.confidence,
                classifier_kind=result.classifier_kind,
                filter_version=result.filter_version,
                details=result.details,
                filtered_at=result.filtered_at,
            )
            for result, raw in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/companies/monitoring/bulk/{action}", response_model=BulkMonitoringStateResponse)
def set_all_monitoring_states(
    action: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BulkMonitoringStateResponse:
    """등록된 모든 기업의 모니터링을 일괄 중지하거나 재개한다."""
    if action == "pause":
        next_status = "paused"
    elif action == "resume":
        next_status = None
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 모니터링 작업입니다.")

    companies = list(db.scalars(select(Company)))
    next_collection_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.realtime_interval_seconds
    )
    for company in companies:
        company.monitoring_status = (
            next_status
            if next_status is not None
            else _resumed_monitoring_status(company)
        )
        company.next_collection_at = next_collection_at
    db.commit()

    resulting_statuses = {company.monitoring_status for company in companies}
    response_status = (
        next(iter(resulting_statuses)) if len(resulting_statuses) == 1 else "mixed"
    )

    return BulkMonitoringStateResponse(
        action=action,
        monitoring_status=response_status,
        updated_count=len(companies),
        total_count=len(companies),
    )


@router.post("/companies/{company_id}/monitoring/{action}", response_model=MonitoringSummary)
def set_monitoring_state(
    company_id: int,
    action: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MonitoringSummary:
    """개별 기업의 모니터링 상태를 중지 또는 재개한 뒤 최신 요약을 반환한다."""
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
        company.monitoring_status = _resumed_monitoring_status(company)
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
    """기업에 연결된 기사를 출처 필터와 페이지 정보에 맞춰 반환한다."""
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    company_articles = (
        select(
            NewsArticle,
            CompanyArticleMatch,
            StoryClusterArticle.story_cluster_id,
            (
                select(func.coalesce(func.sum(ArticleQueryHit.hit_count), 0))
                .where(
                    ArticleQueryHit.raw_article_id == NewsArticle.raw_article_id,
                    ArticleQueryHit.company_id == company_id,
                )
                .correlate(NewsArticle)
                .scalar_subquery()
            ).label("query_hit_count"),
        )
        .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
        .outerjoin(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
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
            positive_probability=article.positive_probability,
            neutral_probability=article.neutral_probability,
            negative_probability=article.negative_probability,
            story_cluster_id=story_cluster_id,
            query_hit_count=query_hit_count,
            anomaly_score=match.anomaly_score,
            is_anomaly=match.is_anomaly,
            created_at=article.created_at,
        )
        for article, match, story_cluster_id, query_hit_count in rows
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
    """기업의 기사 분석, 이상 징후, 기준선 학습 및 다음 수집 상태를 집계한다."""
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
    latest_window = db.scalar(
        select(CompanyFeatureWindow)
        .where(CompanyFeatureWindow.company_id == company_id)
        .order_by(CompanyFeatureWindow.window_start.desc())
        .limit(1)
    )
    valid_nonempty_window_count = db.scalar(
        select(func.count(CompanyFeatureWindow.id)).where(
            CompanyFeatureWindow.company_id == company_id,
            CompanyFeatureWindow.data_quality != "unavailable",
            CompanyFeatureWindow.article_count > 0,
        )
    ) or 0
    ready = (
        article_count >= settings.readiness_min_articles
        and valid_nonempty_window_count >= settings.readiness_min_nonempty_windows
    )
    readiness_status = (
        "active" if company.monitoring_status == "active" else
        "pending_approval" if ready else "preparing"
    )
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
        readiness_status=readiness_status,
        accepted_article_count=article_count,
        valid_nonempty_window_count=valid_nonempty_window_count,
        data_quality=latest_window.data_quality if latest_window else None,
        model_state=latest_window.model_state if latest_window else "unavailable",
    )


@router.get("/companies/{company_id}/risk-events", response_model=list[RiskEventRead])
def list_risk_events(
    company_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    include_legacy: bool = False,
    db: Session = Depends(get_db),
) -> list[RiskEventRead]:
    """기업에서 감지된 최근 위험 이벤트와 관련 기사 정보를 반환한다."""
    if db.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    query = select(RiskEvent).where(
        RiskEvent.company_id == company_id,
        RiskEvent.status != "dismissed",
    )
    if not include_legacy:
        query = query.where(RiskEvent.status != "legacy_candidate")
    events = list(db.scalars(query.order_by(RiskEvent.detected_at.desc()).limit(limit)))
    return [risk_event_read(db, event) for event in events]
