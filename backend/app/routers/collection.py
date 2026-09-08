"""수집 실행, 기사 조회, 필터 감사 및 모니터링 제어 API를 제공한다."""

from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.auth import CurrentAuth, require_auth
from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    ArticleRiskAssessment,
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
    StoryCluster,
    StoryClusterArticle,
    StoryRiskScore,
)
from app.presenters import article_collection_times, risk_event_read
from app.risk_taxonomy import NON_REPORTABLE_RISK_STATUSES, RISK_TYPES
from app.schemas import (
    ArticleFilterResultPage,
    ArticleFilterLlmReviewRead,
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
    RiskJudgmentPageRead,
    RiskJudgmentRead,
    RiskJudgmentSummaryRead,
)
from app.services.llm_labeling import review_article_filter
from app.services.monitoring_pipeline import (
    apply_binary_filter_review,
    continue_accepted_filter_review,
    run_collection,
)
from app.services.period_aggregation import seoul_date_range, seoul_period_start
from app.services.period_story_cohort import (
    canonical_accepted_article_ids, load_period_story_cohort, period_issue_cluster_ids,
)
from app.services.story_risk import source_domain
from app.services.story_model_runtime import resolve_story_risk_runtime


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


def _selected_date_bounds(start_date: date | None, end_date: date | None):
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=422, detail="시작일과 종료일을 함께 선택해 주세요.")
    if start_date is None:
        return None
    try:
        return seoul_date_range(start_date, end_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _date_filters(timestamp, bounds):
    return (timestamp >= bounds[0], timestamp < bounds[1]) if bounds else ()


def _risk_event_time(view: str):
    if view == "history":
        return func.coalesce(
            RiskEvent.closed_at, RiskEvent.last_evidence_at,
            RiskEvent.opened_at, RiskEvent.detected_at,
        )
    return func.coalesce(
        RiskEvent.last_evidence_at, RiskEvent.last_seen_at,
        RiskEvent.opened_at, RiskEvent.detected_at,
    )


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


def _pipeline_filter_result_ids(company_id: int, bounds):
    """One accepted decision per analyzed article plus latest other raw decisions."""
    latest = _latest_filter_results(company_id).subquery()
    accepted = (
        select(func.max(latest.c.id).label("id"))
        .join(NewsArticle, NewsArticle.id == latest.c.curated_article_id)
        .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
        .where(latest.c.decision == "accepted", CompanyArticleMatch.company_id == company_id,
               *_date_filters(func.coalesce(NewsArticle.published_at, NewsArticle.created_at), bounds))
        .group_by(latest.c.curated_article_id)
    )
    other = (
        select(latest.c.id)
        .join(RawNewsArticle, RawNewsArticle.id == latest.c.raw_article_id)
        .where(latest.c.decision != "accepted",
               *_date_filters(func.coalesce(RawNewsArticle.published_at, RawNewsArticle.collected_at), bounds))
    )
    return accepted.union(other)


def _eligible_story_event_ids(min_articles: int):
    """Return story events backed by the minimum number of evidence articles."""
    return (
        select(RiskEventArticle.risk_event_id)
        .group_by(RiskEventArticle.risk_event_id)
        .having(
            func.count(func.distinct(RiskEventArticle.article_id)) >= min_articles
        )
    )


def _reportable_story_event_filters(company_id: int, min_articles: int):
    """위험판정·대응 화면에 노출할 스토리 사건의 공통 조건을 반환한다."""
    return (
        RiskEvent.company_id == company_id,
        RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        RiskEvent.event_source == "story_v2",
        RiskEvent.story_cluster_id.is_not(None),
        RiskEvent.id.in_(_eligible_story_event_ids(min_articles)),
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
    start_date: date | None = None,
    end_date: date | None = None,
    analysis_only: bool = False,
) -> ArticleFilterSummary:
    """기업별 최신 기사 필터 판정을 사유와 처리 방식별로 집계한다."""
    _user_company(db, company_id, auth.user_id)
    bounds = _selected_date_bounds(start_date, end_date)
    latest_query = _latest_filter_results(company_id)
    if analysis_only:
        latest_query = latest_query.where(ArticleFilterResult.id.in_(_pipeline_filter_result_ids(company_id, bounds)))
    elif bounds:
        latest_query = latest_query.join(
            RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id,
        ).where(*_date_filters(
            func.coalesce(RawNewsArticle.published_at, RawNewsArticle.collected_at), bounds,
        ))
    latest = latest_query.subquery()
    raw_records = _latest_filter_results(company_id).join(
        RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id,
    ).where(*_date_filters(func.coalesce(RawNewsArticle.published_at, RawNewsArticle.collected_at), bounds))

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
        raw_record_count=db.scalar(select(func.count()).select_from(raw_records.subquery())) if analysis_only else None,
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
    start_date: date | None = None,
    end_date: date | None = None,
    analysis_only: bool = False,
) -> ArticleFilterResultPage:
    """기업의 최신 기사 필터 결과를 선택 조건과 페이지 단위로 조회한다."""
    _user_company(db, company_id, auth.user_id)
    bounds = _selected_date_bounds(start_date, end_date)
    base = (
        _latest_filter_results(company_id)
        .join(RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id)
        .add_columns(RawNewsArticle)
        .outerjoin(NewsArticle, NewsArticle.id == ArticleFilterResult.curated_article_id)
        .add_columns(NewsArticle)
    )
    if analysis_only:
        base = base.where(ArticleFilterResult.id.in_(_pipeline_filter_result_ids(company_id, bounds)))
    else:
        base = base.where(*_date_filters(
            func.coalesce(RawNewsArticle.published_at, RawNewsArticle.collected_at), bounds,
        ))
    if decision:
        base = base.where(ArticleFilterResult.decision == decision)
    if reason:
        base = base.where(ArticleFilterResult.reason == reason)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(ArticleFilterResult.filtered_at.desc(), ArticleFilterResult.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ArticleFilterResultPage(
        items=[
            ArticleFilterResultRead(
                id=result.id,
                raw_article_id=result.raw_article_id,
                curated_article_id=result.curated_article_id,
                source=article.source if analysis_only and result.decision == "accepted" and article else raw.source,
                title=article.title if analysis_only and result.decision == "accepted" and article else raw.title,
                url=article.url if analysis_only and result.decision == "accepted" and article else raw.url,
                decision=result.decision,
                reason=result.reason,
                relevance_score=result.relevance_score,
                advertising_score=result.advertising_score,
                confidence=result.confidence,
                classifier_kind=result.classifier_kind,
                filter_version=result.filter_version,
                details=result.details,
                filtered_at=result.filtered_at,
                published_at=raw.published_at,
                collected_at=raw.collected_at,
                article_date=(article.published_at or article.created_at)
                if analysis_only and result.decision == "accepted" and article
                else (raw.published_at or raw.collected_at),
            )
            for result, raw, article in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/companies/{company_id}/filter-results/{filter_result_id}/llm-review",
    response_model=ArticleFilterLlmReviewRead,
)
def rereview_filter_result_with_llm(
    company_id: int,
    filter_result_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
) -> ArticleFilterLlmReviewRead:
    """Resolve one review-required article to accepted or rejected using the LLM."""
    company = _user_company(db, company_id, auth.user_id)
    source_result = db.scalar(
        select(ArticleFilterResult).where(
            ArticleFilterResult.id == filter_result_id,
            ArticleFilterResult.company_id == company_id,
        )
    )
    if source_result is None:
        raise HTTPException(status_code=404, detail="정제 결과를 찾을 수 없습니다.")
    latest_id = db.scalar(
        select(ArticleFilterResult.id)
        .where(
            ArticleFilterResult.company_id == company_id,
            ArticleFilterResult.raw_article_id == source_result.raw_article_id,
        )
        .order_by(ArticleFilterResult.id.desc())
        .limit(1)
    )
    if latest_id != source_result.id or source_result.decision != "review_required":
        raise HTTPException(status_code=409, detail="이미 재검토가 완료된 기사입니다.")
    raw = db.get(RawNewsArticle, source_result.raw_article_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="원문 기사를 찾을 수 없습니다.")

    review = review_article_filter(db, company, raw)
    if review is None:
        raise HTTPException(
            status_code=503,
            detail="LLM 재검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        )
    try:
        reviewed_result, article_id = apply_binary_filter_review(
            db,
            company,
            source_result,
            raw,
            review,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if article_id is not None:
        background_tasks.add_task(
            continue_accepted_filter_review,
            company_id,
            article_id,
        )
    llm_review = reviewed_result.details["llm_review"]
    return ArticleFilterLlmReviewRead(
        id=reviewed_result.id,
        raw_article_id=reviewed_result.raw_article_id,
        decision=reviewed_result.decision,
        reason=reviewed_result.reason,
        explanation=llm_review["explanation"],
        confidence=llm_review["confidence"],
        provider=llm_review["provider"],
        model_name=llm_review["model_name"],
        reviewed_at=datetime.fromisoformat(llm_review["reviewed_at"]),
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
    start_date: date | None = None,
    end_date: date | None = None,
    period_basis: Literal["articles", "issues"] = "articles",
    analysis_only: bool = False,
    judged_only: bool = False,
) -> NewsArticlePage:
    """기업에 연결된 기사를 출처·기간·검색어 필터와 페이지 정보에 맞춰 반환한다."""
    _user_company(db, company_id, auth.user_id)
    bounds = _selected_date_bounds(start_date, end_date)
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
    if analysis_only:
        base_query = base_query.where(NewsArticle.id.in_(canonical_accepted_article_ids(company_id)))
    if source:
        base_query = base_query.where(NewsArticle.source == source)
    if q:
        like = f"%{q}%"
        base_query = base_query.where(NewsArticle.title.ilike(like) | NewsArticle.summary.ilike(like))
    # 화면에는 한국 시간으로 날짜가 표시되므로, 필터도 한국 달력 기준 하루로 계산한다.
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    if bounds and period_basis == "issues":
        base_query = base_query.where(
            StoryClusterArticle.story_cluster_id.in_(period_issue_cluster_ids(company_id, *bounds, analysis_only=analysis_only))
            | (StoryClusterArticle.story_cluster_id.is_(None)
               & (article_time >= bounds[0]) & (article_time < bounds[1]))
        )
        if judged_only:
            cohort = load_period_story_cohort(db, company_id, start_date, end_date)
            unjudged = [row["story_cluster_id"] for row in cohort if row["classification"] == "pending"]
            base_query = base_query.where(
                StoryClusterArticle.story_cluster_id.is_(None)
                | StoryClusterArticle.story_cluster_id.notin_(unjudged)
            )
    elif bounds:
        base_query = base_query.where(*_date_filters(article_time, bounds))
    elif isinstance(days, int):
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
        .order_by(NewsArticle.published_at.desc().nullslast(), NewsArticle.created_at.desc(), NewsArticle.id.desc())
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
    risk_filters = (
        _reportable_story_event_filters(company_id, settings.story_event_min_articles)
        if settings.story_risk_engine_enabled
        else (
            RiskEvent.company_id == company_id,
            RiskEvent.status.notin_(NON_REPORTABLE_RISK_STATUSES),
        )
    )
    risk_event_count = db.scalar(
        select(func.count()).select_from(RiskEvent).where(*risk_filters)
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
    model_state = latest_window.model_state if latest_window else "unavailable"
    model_version = latest_window.model_version if latest_window else None
    if settings.story_risk_engine_enabled and settings.story_risk_model_enabled:
        runtime = resolve_story_risk_runtime(settings)
        model_state = runtime.model_state if runtime.available else "unavailable"
        model_version = runtime.version if runtime.available else None
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
        model_state=model_state,
        model_version=model_version,
    )


@router.get("/companies/{company_id}/risk-events/page", response_model=RiskEventPageRead)
def list_risk_events_page(
    company_id: int,
    view: Literal["active", "history", "all"] = "active",
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
    start_date: date | None = None,
    end_date: date | None = None,
) -> RiskEventPageRead:
    """위험관리 화면용 사건 목록을 서버에서 필터링·페이지네이션한다."""
    _user_company(db, company_id, auth.user_id)
    active_statuses = ("open", "monitoring", "acknowledged")
    settings = get_settings()
    bounds = _selected_date_bounds(start_date, end_date)
    base_filters = _reportable_story_event_filters(
        company_id,
        settings.story_event_min_articles,
    )
    issue_latest_dates = {}
    issue_article_ids = {}
    if bounds:
        cohort = load_period_story_cohort(db, company_id, start_date, end_date, settings=settings)
        issue_latest_dates = {row["risk_event_id"]: row["last_evidence_at"] for row in cohort
                              if row["classification"] == "risk"}
        issue_article_ids = {row["risk_event_id"]: row["article_ids"] for row in cohort
                             if row["classification"] == "risk"}
        base_filters += (RiskEvent.id.in_([
            row["risk_event_id"] for row in cohort if row["classification"] == "risk"
        ]),)
    query = select(RiskEvent).where(*base_filters)
    if view == "active":
        query = query.where(RiskEvent.status.in_(active_statuses))
        if bounds is None and isinstance(days, int):
            _, cutoff = seoul_period_start(days)
            query = query.where(
                func.coalesce(
                    RiskEvent.last_evidence_at,
                    RiskEvent.last_seen_at,
                    RiskEvent.opened_at,
                    RiskEvent.detected_at,
                ) >= cutoff
            )
        ordering = (
            func.coalesce(
                RiskEvent.last_evidence_at,
                RiskEvent.last_seen_at,
                RiskEvent.opened_at,
            ).desc(),
            RiskEvent.id.desc(),
        )
    elif view == "history":
        query = query.where(RiskEvent.status == "closed")
        if bounds is None and isinstance(days, int):
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
    else:
        if bounds is None and isinstance(days, int):
            _, cutoff = seoul_period_start(days)
            query = query.where(
                func.coalesce(
                    RiskEvent.last_evidence_at,
                    RiskEvent.last_seen_at,
                    RiskEvent.opened_at,
                    RiskEvent.detected_at,
                ) >= cutoff
            )
        ordering = (
            func.coalesce(
                RiskEvent.last_evidence_at,
                RiskEvent.last_seen_at,
                RiskEvent.opened_at,
                RiskEvent.detected_at,
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
        items=[risk_event_read(db, event, included_article_ids=issue_article_ids.get(event.id)).model_copy(update={
            "issue_latest_at": issue_latest_dates.get(event.id),
        }) for event in events],
        total=total,
        page=page,
        page_size=page_size,
        summary=summary,
    )


def _list_period_risk_judgments(
    db, company_id, start_date, end_date, *, classification, view, page, page_size,
    severity, risk_type, settings, runtime, auth,
):
    cohort = load_period_story_cohort(
        db, company_id, start_date, end_date, settings=settings, runtime=runtime,
        include_unjudged=classification == "pending",
    )
    summary = RiskJudgmentSummaryRead(
        risk=sum(row["classification"] == "risk" for row in cohort),
        non_risk=sum(row["classification"] == "non_risk" for row in cohort),
        pending=sum(row["classification"] == "pending" for row in cohort),
        total=len(cohort),
        active=sum(row["risk_event_status"] in ("open", "monitoring", "acknowledged") for row in cohort),
        history=sum(row["risk_event_status"] == "closed" for row in cohort),
    )
    if classification == "risk":
        risk_page = list_risk_events_page(
            company_id, view=view, page=page, page_size=page_size, days=None,
            severity=severity, risk_type=risk_type, response="all", db=db, auth=auth,
            start_date=start_date, end_date=end_date,
        )
        return RiskJudgmentPageRead(
            items=[RiskJudgmentRead(**item.model_dump(), classification="risk", risk_event_id=item.id)
                   for item in risk_page.items],
            total=risk_page.total, page=page, page_size=page_size, summary=summary,
        )

    matching = sorted(
        (row for row in cohort if row["classification"] == classification),
        key=lambda row: (row["last_evidence_at"], row["story_cluster_id"]), reverse=True,
    )
    selected = matching[(page - 1) * page_size:page * page_size]
    cluster_ids = [row["story_cluster_id"] for row in selected]
    articles_ids = [article_id for row in selected for article_id in row["article_ids"]]
    clusters = {cluster.id: cluster for cluster in db.scalars(
        select(StoryCluster).where(StoryCluster.id.in_(cluster_ids))
    )} if cluster_ids else {}
    evidence_by_cluster = {cluster_id: [] for cluster_id in cluster_ids}
    if articles_ids:
        for assessment, article, link in db.execute(
            select(ArticleRiskAssessment, NewsArticle, StoryClusterArticle)
            .select_from(CompanyArticleMatch)
            .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
            .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
            .outerjoin(ArticleRiskAssessment,
                       (ArticleRiskAssessment.company_id == CompanyArticleMatch.company_id)
                       & (ArticleRiskAssessment.article_id == NewsArticle.id))
            .where(CompanyArticleMatch.company_id == company_id,
                   StoryClusterArticle.story_cluster_id.in_(cluster_ids), NewsArticle.id.in_(articles_ids))
            .order_by(func.coalesce(NewsArticle.published_at, NewsArticle.created_at).desc(), NewsArticle.id.desc())
        ):
            evidence_by_cluster[link.story_cluster_id].append((assessment, article, link))
    collected_times = article_collection_times(db, [
        article for rows in evidence_by_cluster.values() for _assessment, article, _link in rows
    ])
    items = []
    for entry in selected:
        cluster = clusters[entry["story_cluster_id"]]
        rows = evidence_by_cluster[cluster.id]
        primary = next((row[1] for row in rows if row[2].is_representative), rows[0][1] if rows else None)
        risk_rows = [row for row in rows if row[0] is not None and row[0].decision == "risk"
                     and row[0].risk_probability >= settings.article_risk_candidate_threshold]
        domains = {source_domain(article.original_url or article.url) for _assessment, article, _link in rows} - {"unknown"}
        risk_domains = {source_domain(article.original_url or article.url) for _assessment, article, _link in risk_rows} - {"unknown"}
        items.append(RiskJudgmentRead(
            id=cluster.id, company_id=company_id, story_cluster_id=cluster.id,
            classification=classification, risk_event_id=None, pending_reason=entry["pending_reason"],
            article_id=primary.id if primary else None, article_title=primary.title if primary else None,
            article_url=primary.url if primary else None, event_source="story_v2",
            risk_probability=entry["risk_probability"], anomaly_score=entry["anomaly_score"],
            severity=classification, status=classification,
            evidence_articles=[dict(
                article_id=article.id, title=article.title, url=article.url, source=article.source,
                source_domain=source_domain(article.original_url or article.url),
                published_at=article.published_at, created_at=article.created_at,
                collected_at=collected_times.get(article.id),
                evidence_role="trigger" if (assessment, article, link) in risk_rows else "context",
                evidence_score=assessment.risk_probability if assessment else None,
                risk_probability=assessment.risk_probability if assessment else None,
                relevance_score=assessment.relevance_score if assessment else None,
                source_credibility=assessment.source_credibility if assessment else None,
                representativeness=1.0 if link.is_representative else link.similarity,
            ) for assessment, article, link in rows],
            risk_article_count=len(risk_rows), risk_source_count=len(risk_domains),
            evidence_article_count=len(rows), source_count=len(domains),
            summary=cluster.representative_title, model_version=entry["model_version"],
            model_state=entry["model_state"], approval_state="draft",
            opened_at=entry["first_evidence_at"], last_seen_at=entry["last_evidence_at"],
            last_evidence_at=entry["last_evidence_at"], detected_at=entry["first_evidence_at"],
            issue_latest_at=entry["last_evidence_at"],
            response_generation_status="idle",
        ))
    return RiskJudgmentPageRead(items=items, total=len(matching), page=page, page_size=page_size, summary=summary)


@router.get(
    "/companies/{company_id}/risk-judgments/page",
    response_model=RiskJudgmentPageRead,
)
def list_risk_judgments_page(
    company_id: int,
    classification: Literal["risk", "non_risk", "pending"] = "risk",
    view: Literal["active", "history", "all"] = "all",
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
    db: Session = Depends(get_db),
    auth: CurrentAuth = Depends(require_auth),
    start_date: date | None = None,
    end_date: date | None = None,
) -> RiskJudgmentPageRead:
    """스토리 판정 결과를 위험 사건과 비위험 스토리로 나눠 반환한다."""
    _user_company(db, company_id, auth.user_id)
    settings = get_settings()
    story_model_enabled = settings.story_risk_engine_enabled and settings.story_risk_model_enabled
    story_runtime = resolve_story_risk_runtime(settings) if story_model_enabled else None
    bounds = _selected_date_bounds(start_date, end_date)
    if bounds:
        return _list_period_risk_judgments(
            db, company_id, start_date, end_date, classification=classification,
            view=view, page=page, page_size=page_size, severity=severity,
            risk_type=risk_type, settings=settings, runtime=story_runtime, auth=auth,
        )
    if classification == "pending":
        raise HTTPException(status_code=422, detail="미판정 이슈 조회에는 시작일과 종료일이 필요합니다.")
    active_statuses = ("open", "monitoring", "acknowledged")
    event_filters = _reportable_story_event_filters(
        company_id,
        settings.story_event_min_articles,
    )

    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    story_article_count = func.count(func.distinct(CompanyArticleMatch.article_id))
    article_scope = [CompanyArticleMatch.company_id == company_id]
    if story_model_enabled:
        article_scope.extend([
            NewsArticle.created_at <= datetime.now(timezone.utc),
            select(ArticleFilterResult.id).where(
                ArticleFilterResult.company_id == company_id,
                ArticleFilterResult.curated_article_id == NewsArticle.id,
                ArticleFilterResult.decision == "accepted",
            ).exists(),
        ])
    eligible_stories = (
        select(
            StoryClusterArticle.story_cluster_id.label("story_cluster_id"),
            story_article_count.label("evidence_article_count"),
            (
                literal(None)
                if story_model_enabled
                else func.max(ArticleRiskAssessment.risk_probability)
            ).label("risk_probability"),
            func.min(article_time).label("first_evidence_at"),
            func.max(article_time).label("last_evidence_at"),
        )
        .select_from(CompanyArticleMatch)
        .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
        .join(
            StoryClusterArticle,
            StoryClusterArticle.article_id == CompanyArticleMatch.article_id,
        )
        .outerjoin(
            ArticleRiskAssessment,
            (ArticleRiskAssessment.company_id == CompanyArticleMatch.company_id)
            & (ArticleRiskAssessment.article_id == CompanyArticleMatch.article_id),
        )
        .where(*article_scope)
        .group_by(StoryClusterArticle.story_cluster_id)
        .having(story_article_count >= settings.story_event_min_articles)
        .subquery()
    )
    risk_cluster_ids = select(RiskEvent.story_cluster_id).where(*event_filters)
    all_non_risk_story_ids = select(eligible_stories.c.story_cluster_id).where(
        eligible_stories.c.story_cluster_id.notin_(risk_cluster_ids)
    )
    if story_model_enabled:
        # A missing prediction is not a non-risk judgment. The model score is
        # persisted for every assessed story, including stories without events.
        all_non_risk_story_ids = all_non_risk_story_ids.join(
            StoryRiskScore,
            (StoryRiskScore.company_id == company_id)
            & (StoryRiskScore.story_cluster_id == eligible_stories.c.story_cluster_id),
        )
        if story_runtime.available:
            all_non_risk_story_ids = all_non_risk_story_ids.where(
                StoryRiskScore.model_version == story_runtime.version,
            )
    non_risk_story_ids = all_non_risk_story_ids
    if bounds:
        non_risk_story_ids = non_risk_story_ids.where(
            *_date_filters(eligible_stories.c.last_evidence_at, bounds)
        )
    elif isinstance(days, int):
        _, cutoff = seoul_period_start(days)
        non_risk_story_ids = non_risk_story_ids.where(
            eligible_stories.c.last_evidence_at >= cutoff
        )

    # 요약과 목록이 같은 기준으로 세게 맞춘다. _date_filters는 bounds(명시적 날짜 범위)가
    # 있을 때만 조건을 만들어서, 화면이 days("최근 7일")로 조회하면 요약은 전체 기간을
    # 세고 아래 목록(list_risk_events_page)은 days를 반영해 서로 다른 숫자가 나왔다
    # (실측 쿠팡: 요약 13건 vs 목록 0건). days일 때도 목록과 같은 시각 기준을 건다.
    risk_time_filters = _date_filters(_risk_event_time(view), bounds)
    if bounds is None and isinstance(days, int):
        _, risk_cutoff = seoul_period_start(days)
        risk_time_filters = (_risk_event_time(view) >= risk_cutoff,)

    active_count = db.scalar(
        select(func.count(RiskEvent.id)).where(
            *event_filters,
            *risk_time_filters,
            RiskEvent.status.in_(active_statuses),
        )
    ) or 0
    history_count = db.scalar(
        select(func.count(RiskEvent.id)).where(
            *event_filters,
            *risk_time_filters,
            RiskEvent.status == "closed",
        )
    ) or 0
    filtered_non_risk_count = db.scalar(
        select(func.count()).select_from(non_risk_story_ids.subquery())
    ) or 0
    summary = RiskJudgmentSummaryRead(
        risk=active_count + history_count,
        # non_risk_story_ids는 bounds와 days를 모두 반영해 걸러 둔 것이라 그대로 쓴다.
        # 예전에는 days일 때 걸러지지 않은 수를 골라 위험 건수와 기준이 어긋났다.
        non_risk=filtered_non_risk_count,
        active=active_count,
        history=history_count,
    )

    if classification == "risk":
        risk_page = list_risk_events_page(
            company_id=company_id,
            view=view,
            page=page,
            page_size=page_size,
            days=days,
            start_date=start_date,
            end_date=end_date,
            severity=severity,
            risk_type=risk_type,
            response="all",
            db=db,
            auth=auth,
        )
        return RiskJudgmentPageRead(
            items=[
                RiskJudgmentRead(
                    **item.model_dump(),
                    classification="risk",
                    risk_event_id=item.id,
                )
                for item in risk_page.items
            ],
            total=risk_page.total,
            page=page,
            page_size=page_size,
            summary=summary,
        )

    non_risk_query = (
        select(
            StoryCluster,
            eligible_stories.c.risk_probability,
            eligible_stories.c.first_evidence_at,
            eligible_stories.c.last_evidence_at,
        )
        .join(
            eligible_stories,
            eligible_stories.c.story_cluster_id == StoryCluster.id,
        )
        .where(StoryCluster.id.in_(non_risk_story_ids))
    )
    story_rows = db.execute(
        non_risk_query
        .order_by(
            eligible_stories.c.last_evidence_at.desc().nullslast(),
            StoryCluster.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    cluster_ids = [cluster.id for cluster, *_rest in story_rows]
    story_scores = {
        score.story_cluster_id: score
        for score in db.scalars(
            select(StoryRiskScore).where(
                StoryRiskScore.company_id == company_id,
                StoryRiskScore.story_cluster_id.in_(cluster_ids),
            )
        )
    } if story_model_enabled and cluster_ids else {}
    evidence_by_cluster: dict[
        int,
        list[tuple[ArticleRiskAssessment | None, NewsArticle, StoryClusterArticle]],
    ] = {cluster_id: [] for cluster_id in cluster_ids}
    if cluster_ids:
        evidence_rows = db.execute(
            select(ArticleRiskAssessment, NewsArticle, StoryClusterArticle)
            .select_from(CompanyArticleMatch)
            .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
            .join(
                StoryClusterArticle,
                StoryClusterArticle.article_id == CompanyArticleMatch.article_id,
            )
            .outerjoin(
                ArticleRiskAssessment,
                (ArticleRiskAssessment.company_id == CompanyArticleMatch.company_id)
                & (ArticleRiskAssessment.article_id == CompanyArticleMatch.article_id),
            )
            .where(
                *article_scope,
                *_date_filters(article_time, bounds),
                StoryClusterArticle.story_cluster_id.in_(cluster_ids),
            )
            .order_by(
                StoryClusterArticle.story_cluster_id,
                ArticleRiskAssessment.risk_probability.desc().nullslast(),
                article_time.desc(),
            )
        ).all()
        for assessment, article, cluster_link in evidence_rows:
            evidence_by_cluster[cluster_link.story_cluster_id].append(
                (assessment, article, cluster_link)
            )

    collected_times = article_collection_times(db, [
        article for rows in evidence_by_cluster.values() for _assessment, article, _link in rows
    ])
    items: list[RiskJudgmentRead] = []
    for cluster, probability, first_evidence_at, last_evidence_at in story_rows:
        story_score = story_scores.get(cluster.id)
        rows = evidence_by_cluster.get(cluster.id, [])
        primary_row = next(
            (row for row in rows if row[2].is_representative),
            rows[0] if rows else None,
        )
        type_scores: dict[str, float] = {}
        for assessment, _article, _cluster_link in rows:
            if assessment is None:
                continue
            for key, raw_score in (assessment.type_scores or {}).items():
                if key not in RISK_TYPES:
                    continue
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    continue
                type_scores[key] = max(type_scores.get(key, 0.0), score)
        primary_type = (
            max(type_scores.items(), key=lambda item: item[1])[0]
            if type_scores
            else None
        )
        risk_rows = [
            row
            for row in rows
            if row[0] is not None
            and row[0].decision == "risk"
            and row[0].risk_probability >= settings.article_risk_candidate_threshold
        ]
        evidence_domains = {
            source_domain(article.original_url or article.url)
            for _assessment, article, _cluster_link in rows
        } - {"unknown"}
        risk_domains = {
            source_domain(article.original_url or article.url)
            for _assessment, article, _cluster_link in risk_rows
        } - {"unknown"}
        primary_article = primary_row[1] if primary_row else None
        detected_at = first_evidence_at or last_evidence_at or datetime.now(timezone.utc)
        items.append(
            RiskJudgmentRead(
                id=cluster.id,
                classification="non_risk",
                risk_event_id=None,
                company_id=company_id,
                article_id=primary_article.id if primary_article else None,
                article_title=primary_article.title if primary_article else None,
                article_url=primary_article.url if primary_article else None,
                story_cluster_id=cluster.id,
                event_source="story_v2",
                anomaly_score=float(story_score.anomaly_score) if story_score is not None else 0.0,
                risk_probability=(
                    float(story_score.risk_probability)
                    if story_score is not None
                    else float(probability or 0.0)
                ),
                severity="non_risk",
                status="non_risk",
                primary_type=primary_type,
                risk_types=[
                    {
                        "risk_type": key,
                        "probability": score,
                        "is_primary": key == primary_type,
                        "evidence": {"source": "article_assessments"},
                    }
                    for key, score in sorted(
                        type_scores.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    if score >= 0.35 or key == primary_type
                ],
                evidence_articles=[
                    {
                        "article_id": article.id,
                        "title": article.title,
                        "url": article.url,
                        "source": article.source,
                        "source_domain": source_domain(article.original_url or article.url),
                        "published_at": article.published_at,
                        "created_at": article.created_at,
                        "collected_at": collected_times.get(article.id),
                        "evidence_role": (
                            "trigger"
                            if assessment is not None
                            and assessment.decision == "risk"
                            and assessment.risk_probability
                            >= settings.article_risk_candidate_threshold
                            else "context"
                        ),
                        "evidence_score": assessment.risk_probability if assessment else 0.0,
                        "risk_probability": assessment.risk_probability if assessment else None,
                        "relevance_score": assessment.relevance_score if assessment else None,
                        "type_match_score": (
                            (assessment.type_scores or {}).get(primary_type)
                            if assessment is not None and primary_type
                            else None
                        ),
                        "source_credibility": assessment.source_credibility if assessment else None,
                        "representativeness": (
                            1.0 if cluster_link.is_representative else cluster_link.similarity
                        ),
                    }
                    for assessment, article, cluster_link in rows
                ],
                risk_article_count=len(risk_rows),
                risk_source_count=len(risk_domains),
                evidence_article_count=len(rows),
                source_count=len(evidence_domains),
                summary=cluster.representative_title,
                model_version=(
                    story_score.model_version
                    if story_score is not None
                    else primary_row[0].model_version
                    if primary_row and primary_row[0] is not None
                    else None
                ),
                model_state=story_score.model_state if story_score is not None else "provisional",
                approval_state="draft",
                opened_at=first_evidence_at,
                last_seen_at=last_evidence_at,
                last_evidence_at=last_evidence_at,
                response_generation_status="idle",
                detected_at=detected_at,
            )
        )
    return RiskJudgmentPageRead(
        items=items,
        total=filtered_non_risk_count,
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
