"""수집 실행, 기사 조회, 필터 감사 및 모니터링 제어 API를 제공한다."""

from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
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
    RiskEventArticle,
    RiskEventType,
    StoryClusterArticle,
)
from app.presenters import risk_event_read
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES, RISK_TYPES
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
    RiskEventPageRead,
    RiskEventRead,
    RiskEventSummaryRead,
)
from app.services.monitoring_pipeline import run_collection
from app.services.period_aggregation import seoul_period_start


router = APIRouter(tags=["collection"])
SEOUL = ZoneInfo("Asia/Seoul")


def _user_company(db: Session, company_id: int, user_id: int) -> Company:
    """Return a company only when it belongs to the current user."""
    company = db.scalar(
        select(Company).where(
            Company.id == company_id,
            Company.user_id == user_id,
        )
    )
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    return company


def _resumed_monitoring_status(company: Company) -> str:
    """Resume collection immediately while model analysis can continue warming up."""
    return "active"


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


def _eligible_story_event_ids(min_articles: int):
    """Return story events backed by the minimum number of evidence articles."""
    return (
        select(RiskEventArticle.risk_event_id)
        .group_by(RiskEventArticle.risk_event_id)
        .having(
            func.count(func.distinct(RiskEventArticle.article_id)) >= min_articles
        )
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
def get_provider_status(
    settings: Settings = Depends(get_settings),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionProviderStatus:
    """현재 뉴스 수집 제공자의 활성화 상태를 반환한다."""
    return provider_status(settings)


@router.post("/companies/{company_id}/collect", response_model=CollectionJobRead)
def collect_company_news(
    company_id: int,
    payload: CollectionRequest,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionJob:
    """지정 기업에 대해 사용자가 요청한 기간·소스로 수동 뉴스 수집을 실행한다."""
    company = _user_company(db, company_id, auth.user_id)
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
    auth: CurrentAuth = Depends(require_auth),
) -> CollectionJobPage:
    """기업의 뉴스 수집 작업 이력을 최신순으로 페이지네이션해 반환한다."""
    _user_company(db, company_id, auth.user_id)
    query = select(CollectionJob).where(
        CollectionJob.company_id == company_id,
        CollectionJob.user_id == auth.user_id,
    )
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
    auth: CurrentAuth = Depends(require_auth),
) -> ArticleFilterSummary:
    """기업별 최신 기사 필터 판정을 사유와 처리 방식별로 집계한다."""
    _user_company(db, company_id, auth.user_id)
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
    auth: CurrentAuth = Depends(require_auth),
) -> ArticleFilterResultPage:
    """기업의 최신 기사 필터 결과를 선택 조건과 페이지 단위로 조회한다."""
    _user_company(db, company_id, auth.user_id)
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
    auth: CurrentAuth = Depends(require_auth),
) -> BulkMonitoringStateResponse:
    """등록된 모든 기업의 모니터링을 일괄 중지하거나 재개한다."""
    if action == "pause":
        next_status = "paused"
    elif action == "resume":
        next_status = None
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 모니터링 작업입니다.")

    companies = list(
        db.scalars(select(Company).where(Company.user_id == auth.user_id))
    )
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
    auth: CurrentAuth = Depends(require_auth),
) -> MonitoringSummary:
    """개별 기업의 모니터링 상태를 중지 또는 재개한 뒤 최신 요약을 반환한다."""
    company = _user_company(db, company_id, auth.user_id)
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
    return get_monitoring_summary(company_id, db, settings, auth)


@router.get("/companies/{company_id}/articles", response_model=NewsArticlePage)
def list_company_articles(
    company_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=1000),
    source: str | None = Query(default=None, min_length=1, max_length=40),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    time_from: datetime | None = Query(default=None),
    time_to: datetime | None = Query(default=None),
    days: int | None = Query(default=None, ge=1, le=365),
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> NewsArticlePage:
    """기업에 연결된 기사를 출처·기간·검색어 필터와 페이지 정보에 맞춰 반환한다."""
    _user_company(db, company_id, auth.user_id)
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
    base_query = company_articles
    if source:
        base_query = base_query.where(NewsArticle.source == source)
    if q:
        like = f"%{q}%"
        base_query = base_query.where(NewsArticle.title.ilike(like) | NewsArticle.summary.ilike(like))
    # 화면에는 한국 시간으로 날짜가 표시되므로, 필터도 한국 달력 기준 하루로 계산한다.
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    if isinstance(days, int):
        _, period_start = seoul_period_start(days)
        base_query = base_query.where(article_time >= period_start)
    if date_from:
        base_query = base_query.where(article_time >= datetime.combine(date_from, datetime.min.time(), tzinfo=SEOUL).astimezone(timezone.utc))
    if date_to:
        base_query = base_query.where(article_time < (datetime.combine(date_to, datetime.min.time(), tzinfo=SEOUL) + timedelta(days=1)).astimezone(timezone.utc))
    if time_from:
        base_query = base_query.where(article_time >= time_from)
    if time_to:
        base_query = base_query.where(article_time < time_to)
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
    auth: CurrentAuth = Depends(require_auth),
) -> MonitoringSummary:
    """기업의 기사 분석, 이상 징후, 기준선 학습 및 다음 수집 상태를 집계한다."""
    company = _user_company(db, company_id, auth.user_id)
    article_count = db.scalar(
        select(func.count()).select_from(CompanyArticleMatch).where(
            CompanyArticleMatch.company_id == company_id
        )
    ) or 0
    risk_event_count = db.scalar(
        select(func.count()).select_from(RiskEvent).where(
            RiskEvent.company_id == company_id,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
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
    readiness_status = "active"
    return MonitoringSummary(
        company_id=company_id,
        monitoring_status=company.monitoring_status,
        analysis_status=company.analysis_status,
        article_count=article_count,
        risk_event_count=risk_event_count,
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


@router.get("/companies/{company_id}/risk-events/page", response_model=RiskEventPageRead)
def list_risk_events_page(
    company_id: int,
    view: Literal["active", "history"] = "active",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    days: int | None = Query(default=None, ge=1, le=36500),
    severity: Literal["warning", "critical"] | None = None,
    risk_type: Literal[
        "product_quality",
        "safety_accident",
        "security_privacy",
        "legal_regulatory",
        "labor_hr",
        "financial_governance",
        "supply_operations",
        "reputation_consumer",
    ] | None = None,
    response: Literal["all", "needs_action", "without_needs_action", "in_progress", "generated", "none"] = "all",
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> RiskEventPageRead:
    """위험관리 화면용 사건 목록을 서버에서 필터링·페이지네이션한다."""
    _user_company(db, company_id, auth.user_id)
    active_statuses = ("open", "monitoring", "acknowledged")
    settings = get_settings()
    base_filters = (
        RiskEvent.company_id == company_id,
        RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        RiskEvent.event_source == "story_v2",
        RiskEvent.story_cluster_id.is_not(None),
        RiskEvent.id.in_(
            _eligible_story_event_ids(settings.story_event_min_articles)
        ),
    )
    query = select(RiskEvent).where(*base_filters)
    if view == "active":
        query = query.where(RiskEvent.status.in_(active_statuses))
        ordering = (
            func.coalesce(
                RiskEvent.last_evidence_at,
                RiskEvent.last_seen_at,
                RiskEvent.opened_at,
            ).desc(),
            RiskEvent.id.desc(),
        )
    else:
        query = query.where(RiskEvent.status == "closed")
        if days is not None:
            _, cutoff = seoul_period_start(days)
            query = query.where(
                func.coalesce(
                    RiskEvent.closed_at,
                    RiskEvent.last_evidence_at,
                    RiskEvent.opened_at,
                ) >= cutoff
            )
        ordering = (
            func.coalesce(
                RiskEvent.closed_at,
                RiskEvent.last_evidence_at,
                RiskEvent.opened_at,
            ).desc(),
            RiskEvent.id.desc(),
        )
    if severity is not None:
        query = query.where(RiskEvent.severity == severity)
    if risk_type is not None:
        # Literal 검증과 별개로 이 조건을 유지해 쿼리 계약과 분류 체계를 함께 고정한다.
        if risk_type not in RISK_TYPES:
            raise HTTPException(status_code=422, detail="지원하지 않는 위험 유형입니다.")
        query = query.where(
            RiskEvent.id.in_(
                select(RiskEventType.risk_event_id).where(
                    RiskEventType.risk_type == risk_type
                )
            )
        )
    response_statuses = {
        "needs_action": ("idle", "deferred", "failed"),
        "without_needs_action": ("pending", "generating", "generated"),
        "in_progress": ("pending", "generating"),
        "generated": ("generated",),
        "none": ("idle",),
    }
    if response != "all":
        query = query.where(
            RiskEvent.response_generation_status.in_(response_statuses[response])
        )

    total = db.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    ) or 0
    events = list(
        db.scalars(
            query.order_by(*ordering)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    def summary_count(*extra_filters) -> int:
        return db.scalar(
            select(func.count(RiskEvent.id)).where(*base_filters, *extra_filters)
        ) or 0

    summary = RiskEventSummaryRead(
        active=summary_count(RiskEvent.status.in_(active_statuses)),
        critical=summary_count(
            RiskEvent.status.in_(active_statuses),
            RiskEvent.severity == "critical",
        ),
        needs_response=summary_count(
            RiskEvent.status.in_(active_statuses),
            RiskEvent.response_generation_status.in_(("idle", "deferred", "failed")),
        ),
        history=summary_count(RiskEvent.status == "closed"),
    )
    return RiskEventPageRead(
        items=[risk_event_read(db, event) for event in events],
        total=total,
        page=page,
        page_size=page_size,
        summary=summary,
    )


@router.get("/companies/{company_id}/risk-events", response_model=list[RiskEventRead])
def list_risk_events(
    company_id: int,
    limit: int = Query(default=50, ge=1, le=1000),
    days: int | None = Query(default=None, ge=1, le=365),
    include_legacy: bool = False,
    view: Literal["active", "history", "all"] = "active",
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> list[RiskEventRead]:
    """기업에서 감지된 최근 위험 이벤트와 관련 기사 정보를 반환한다."""
    _user_company(db, company_id, auth.user_id)
    query = select(RiskEvent).where(
        RiskEvent.company_id == company_id,
        RiskEvent.status != "dismissed",
    )
    if view == "active":
        query = query.where(RiskEvent.status.in_(["open", "monitoring", "acknowledged"]))
    elif view == "history":
        query = query.where(RiskEvent.status == "closed")
    if isinstance(days, int):
        _, cutoff = seoul_period_start(days)
        query = query.where(RiskEvent.opened_at >= cutoff)
    if not include_legacy:
        query = query.where(RiskEvent.status != "legacy_candidate")
    if view == "history":
        ordering = (
            func.coalesce(RiskEvent.closed_at, RiskEvent.last_evidence_at, RiskEvent.opened_at).desc(),
            RiskEvent.id.desc(),
        )
    else:
        ordering = (
            func.coalesce(RiskEvent.last_evidence_at, RiskEvent.last_seen_at, RiskEvent.opened_at).desc(),
            RiskEvent.id.desc(),
        )
    events = list(db.scalars(query.order_by(*ordering).limit(limit)))
    return [risk_event_read(db, event) for event in events]
