"""외부 기사 수집부터 필터링·감성 분석·이상 탐지까지 전체 흐름을 조정한다."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from threading import Lock
from uuid import uuid4

from sqlalchemy import delete, or_, select, text

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    ArticleQueryHit,
    ArticleFilterResult,
    ArticleRiskAssessment,
    CollectionIncident,
    CollectionJob,
    Company,
    CompanyArticleMatch,
    CompanyKeyword,
    NewsArticle,
    RawNewsArticle,
)
from app.services.article_filtering import (
    FilterConfig,
    TOPICAL_RELEVANCE_TRAINED_COMPANIES,
    classify_article,
    content_hash,
    get_semantic_scorer,
    normalized_content,
    normalize_text,
    normalize_url,
)
from app.services.collection_health import (
    complete_retry,
    dispatch_pending_notifications,
    evaluate_attempts,
    floor_window,
    record_pipeline_failure,
    record_attempts,
)
from app.services.news_collectors import (
    KakaoDaumSearchCollector,
    NaverNewsCollector,
    TavilyNewsCollector,
    YouTubeCommentCollector,
)
from app.services.llm_labeling import enqueue_llm_labeling_for_company
from app.services.fine_tuned_text import (
    predict_relevance_batch,
    predict_topical_relevance_batch,
)
from app.services.company_reranker import predict_company_relevance_batch
from app.services.sentiment import analyze_company_articles
from app.services.risk_analysis import (
    backfill_historical_windows,
    build_feature_window,
    reanalyze_historical_windows,
)
from app.services.story_clustering import assign_story_cluster
from app.services.story_risk import (
    close_stale_story_events,
    enqueue_company_risk_articles,
    process_company_risk_articles,
)
from app.services.model_operations import SEOUL, ensure_daily_model_check


logger = logging.getLogger(__name__)
_last_model_operation_check_date = None
_reanalysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-reanalysis")
_reanalysis_lock = Lock()
_reanalysis_statuses: dict[int, dict[str, int | str]] = {}

SOURCE_ALIASES = {
    "naver": "naver_api_hub",
    "kakao": "kakao_daum",
    "youtube": "youtube_comment",
    "tavily": "tavily",
}


def _canonical_sources(sources: list[str]) -> list[str]:
    return list(dict.fromkeys(SOURCE_ALIASES.get(source, source) for source in sources))


def _article_filter_config(settings: Settings) -> FilterConfig:
    """애플리케이션 설정을 기사 필터 전용 구성 객체로 변환한다."""
    return FilterConfig(
        version=settings.article_filter_version,
        duplicate_threshold=settings.article_filter_duplicate_threshold,
        advertising_reject_threshold=(
            settings.article_filter_advertising_reject_threshold
        ),
        advertising_review_threshold=(
            settings.article_filter_advertising_review_threshold
        ),
        relevance_accept_threshold=(
            settings.article_filter_relevance_accept_threshold
        ),
        relevance_reject_threshold=(
            settings.article_filter_relevance_reject_threshold
        ),
        ai_enabled=settings.article_filter_ai_enabled,
        classifier_model_name=settings.article_filter_classifier_model,
        semantic_model_name=settings.article_filter_semantic_model,
        allow_model_download=settings.article_filter_allow_model_download,
    )


def _raw_candidates(db, raw: RawNewsArticle, limit: int = 250) -> list[RawNewsArticle]:
    """현재 원문 기사와 중복일 가능성이 있는 과거 원문 후보를 조회한다."""
    exact = or_(
        RawNewsArticle.normalized_url == raw.normalized_url,
        RawNewsArticle.content_hash == raw.content_hash,
        RawNewsArticle.title == raw.title,
    )
    candidates = list(
        db.scalars(
            select(RawNewsArticle)
            .where(RawNewsArticle.id != raw.id, exact)
            .order_by(RawNewsArticle.collected_at.desc())
            .limit(limit)
        )
    )
    remaining = limit - len(candidates)
    if remaining <= 0:
        return candidates

    # 정규화 전 제목 표기가 조금 다른 경우도 15분 범위에서 비교하되, 7일치
    # 전체 후보에 밀려 정확한 제목 후보가 누락되지 않도록 시간 범위를 좁힌다.
    nearby_conditions = []
    if raw.published_at is not None:
        nearby_conditions.append(
            RawNewsArticle.published_at.between(
                raw.published_at - timedelta(minutes=15),
                raw.published_at + timedelta(minutes=15),
            )
        )
    if raw.collected_at is not None:
        nearby_conditions.append(
            RawNewsArticle.collected_at.between(
                raw.collected_at - timedelta(minutes=15),
                raw.collected_at + timedelta(minutes=15),
            )
        )
    if nearby_conditions:
        existing_ids = {candidate.id for candidate in candidates}
        candidates.extend(
            candidate
            for candidate in db.scalars(
                select(RawNewsArticle)
                .where(
                    RawNewsArticle.id != raw.id,
                    RawNewsArticle.id.notin_(existing_ids),
                    or_(*nearby_conditions),
                )
                .order_by(RawNewsArticle.collected_at.desc())
                .limit(remaining)
            )
        )
    return candidates


def _lock_normalized_url(db, normalized_url: str) -> None:
    """Serialize one canonical URL across concurrent collectors in PostgreSQL."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:url, 0))"),
            {"url": normalized_url},
        )


def _raw_for_content(
    db,
    source: str,
    normalized_url: str,
    item_content_hash: str,
) -> RawNewsArticle | None:
    """같은 제공자·URL·내용인 원문만 재사용해 변하는 목록 페이지의 이력을 보존한다."""
    return db.scalar(
        select(RawNewsArticle)
        .where(
            RawNewsArticle.source == source,
            RawNewsArticle.normalized_url == normalized_url,
            RawNewsArticle.content_hash == item_content_hash,
        )
        .order_by(RawNewsArticle.id)
        .limit(1)
    )


def _lock_curated_raw(db, raw_article_id: int) -> None:
    """Serialize creation of the one curated row allowed for a raw article."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"news-article-raw:{raw_article_id}"},
        )


def _lock_company_collection(db, company_id: int) -> None:
    """Serialize collection runs for one company so overlapping triggers
    (scheduler tick, incident retry, manual run) can't both compute the same
    stale existing_match_ids and race each other into company_article_matches."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"company-collection:{company_id}"},
        )


def _curated_for_raw(db, raw_article_id: int | None) -> NewsArticle | None:
    """원문 기사에 연결된 정제 기사를 직접 또는 필터 결과를 통해 찾는다."""
    if raw_article_id is None:
        return None
    article = db.scalar(
        select(NewsArticle).where(NewsArticle.raw_article_id == raw_article_id)
    )
    if article is not None:
        return article
    return db.scalar(
        select(NewsArticle)
        .join(
            ArticleFilterResult,
            ArticleFilterResult.curated_article_id == NewsArticle.id,
        )
        .where(ArticleFilterResult.raw_article_id == raw_article_id)
        .order_by(ArticleFilterResult.filtered_at.desc())
        .limit(1)
    )


def _curated_for_raw_or_url(
    db,
    raw_article_id: int,
    normalized_url: str,
) -> NewsArticle | None:
    """URL 정규화 규칙이 바뀌어도 동일 원문의 기존 정제 기사를 우선 재사용한다."""
    article = _curated_for_raw(db, raw_article_id)
    if article is not None:
        return article
    return db.scalar(
        select(NewsArticle).where(NewsArticle.url == normalized_url)
    )


def _reuse_existing_curated_article(
    decision,
    raw: RawNewsArticle,
    article: NewsArticle,
) -> None:
    """기존 정제 기사를 재사용할 때 다른 원문인 경우에만 중복 연결로 기록한다."""
    if decision.reason != "accepted":
        return
    canonical_raw_id = article.raw_article_id
    if canonical_raw_id is None or canonical_raw_id == raw.id:
        decision.details["existing_curated_reused"] = True
        return
    decision.reason = "duplicate"
    decision.duplicate_of_raw_id = canonical_raw_id
    decision.details["duplicate_evidence"] = "existing_curated_url"


def _get_or_create_curated_article(
    db,
    raw: RawNewsArticle,
    item,
    normalized_url: str,
) -> tuple[NewsArticle, bool]:
    """Create exactly one curated article across retries and concurrent companies."""
    # URL uniqueness protects different raw rows for the same canonical URL;
    # raw-ID uniqueness protects the same row when URL normalization evolves.
    _lock_normalized_url(db, normalized_url)
    _lock_curated_raw(db, raw.id)
    article = _curated_for_raw_or_url(db, raw.id, normalized_url)
    if article is not None:
        return article, False
    article = NewsArticle(
        raw_article_id=raw.id,
        source=item.source,
        title=item.title,
        summary=item.summary,
        url=normalized_url,
        original_url=item.original_url or item.url,
        published_at=item.published_at,
        raw_payload=item.raw_payload,
    )
    db.add(article)
    db.flush()
    return article, True


def apply_binary_filter_review(
    db,
    company: Company,
    source_result: ArticleFilterResult,
    raw: RawNewsArticle,
    review: dict,
) -> tuple[ArticleFilterResult, int | None]:
    """Persist an LLM re-review as a new immutable latest filter decision.

    A separate result row preserves the original model judgement and prevents a
    later rules-only historical reanalysis from silently overwriting the manual
    LLM override. Accepted articles are materialized and linked exactly as they
    are during collection so downstream analysis can continue normally.
    """
    _lock_company_collection(db, company.id)
    latest = db.scalar(
        select(ArticleFilterResult)
        .where(
            ArticleFilterResult.company_id == company.id,
            ArticleFilterResult.raw_article_id == raw.id,
        )
        .order_by(ArticleFilterResult.id.desc())
        .limit(1)
        .with_for_update()
    )
    if latest is None or latest.id != source_result.id or latest.decision != "review_required":
        raise ValueError("이미 처리되었거나 최신 상태가 아닌 정제 결과입니다.")

    now = datetime.now(timezone.utc)
    decision = review["decision"]
    reason = review["reason"]
    duplicate_of_raw_id = None
    article = None

    if decision == "accepted":
        article, _created = _get_or_create_curated_article(
            db,
            raw,
            raw,
            raw.normalized_url,
        )
        if article.raw_article_id is not None and article.raw_article_id != raw.id:
            reason = "duplicate"
            duplicate_of_raw_id = article.raw_article_id

    review_details = {
        "source_filter_result_id": source_result.id,
        "source_filter_version": source_result.filter_version,
        "provider": review["provider"],
        "model_name": review["model_name"],
        "decision": review["decision"],
        "reason": review["reason"],
        "confidence": float(review["confidence"]),
        "explanation": str(review["explanation"])[:4000],
        "reviewed_at": now.isoformat(),
        "ambiguous_policy": "rejected",
    }
    reviewed_result = ArticleFilterResult(
        raw_article_id=raw.id,
        company_id=company.id,
        decision=decision,
        reason=reason,
        duplicate_of_raw_id=duplicate_of_raw_id,
        curated_article_id=article.id if article is not None else None,
        relevance_score=float(review["relevance_score"]),
        advertising_score=float(review["advertising_score"]),
        confidence=float(review["confidence"]),
        classifier_kind="llm_binary_review",
        filter_version=(
            f"{source_result.filter_version[:40]}:llm-review:{uuid4().hex[:12]}"
        ),
        details={
            **dict(source_result.details or {}),
            "llm_review": review_details,
        },
        filtered_at=now,
    )
    db.add(reviewed_result)
    db.flush()

    if article is not None:
        match = db.get(CompanyArticleMatch, (company.id, article.id))
        if match is None:
            hit = db.scalar(
                select(ArticleQueryHit)
                .where(
                    ArticleQueryHit.company_id == company.id,
                    ArticleQueryHit.raw_article_id == raw.id,
                )
                .order_by(ArticleQueryHit.last_seen_at.desc(), ArticleQueryHit.id.desc())
                .limit(1)
            )
            db.add(
                CompanyArticleMatch(
                    company_id=company.id,
                    article_id=article.id,
                    job_id=hit.job_id if hit is not None else None,
                    matched_keyword=hit.matched_keyword if hit is not None else None,
                )
            )
        assign_story_cluster(db, article, company_id=company.id)
        db.flush()

    return reviewed_result, article.id if article is not None else None


def continue_accepted_filter_review(company_id: int, article_id: int) -> None:
    """Run the normal downstream stages after an LLM review accepts an article."""
    analyze_company_articles(company_id)
    enqueue_company_risk_articles(company_id, [article_id])


def _realtime_sources(settings: Settings) -> list[str]:
    """자격 증명이 준비된 실시간 수집 소스 목록을 구성한다."""
    sources: list[str] = []
    if settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret:
        sources.append("naver_api_hub")
    if settings.kakao_rest_api_key:
        sources.append("kakao_daum")
    if settings.youtube_api_key:
        sources.append("youtube_comment")
    return sources


def _youtube_realtime_due(scheduled_for: datetime, settings: Settings) -> bool:
    """YouTube Data API 무료 쿼터(하루 10,000유닛) 안에서 반복 실시간 수집이 가능한 주기인지 확인한다.

    검색 1회는 100유닛, 영상당 댓글 조회가 최대 5회(1유닛)라 쿼리당 최대 105유닛이다.
    매 15분 틱마다 돌리면 회사 9곳만으로도 하루 한도를 몇 번 만에 소진하므로,
    설정된 시간 간격(기본 3시간, 즉 하루 8회)에 맞는 15분 구간에서만 실행한다.
    """
    interval_minutes = max(15, settings.youtube_realtime_interval_hours * 60)
    epoch_minutes = int(scheduled_for.timestamp() // 60)
    return epoch_minutes % interval_minutes == 0


def _throttle_youtube_for_tick(sources: list[str], scheduled_for: datetime, settings: Settings) -> list[str]:
    """정기 실시간 틱에서만 유튜브를 쿼터 안전 주기로 제한하고, 그 외 호출은 그대로 둔다."""
    if "youtube_comment" not in sources or _youtube_realtime_due(scheduled_for, settings):
        return sources
    return [source for source in sources if source != "youtube_comment"]


def _incident_retry_sources(settings: Settings, sources: list[str]) -> list[str]:
    """장애 기록용 가상 소스를 재시도 가능한 실제 수집 소스로 치환한다."""
    canonical = _canonical_sources(sources)
    if "pipeline" not in canonical:
        return canonical
    configured = _realtime_sources(settings) or ["naver_api_hub"]
    return list(
        dict.fromkeys(
            [source for source in canonical if source != "pipeline"] + configured
        )
    )


def _backfill_sources(settings: Settings) -> list[str]:
    """실시간 소스에 과거 검색용 제공자를 더해 백필 소스 목록을 구성한다."""
    sources = _realtime_sources(settings)
    if settings.tavily_api_key:
        sources.append("tavily")
    return list(dict.fromkeys(sources))


def build_queries(
    company: Company,
    keywords: list[CompanyKeyword],
    limit: int | None = None,
    include_company_query: bool = True,
) -> list[str]:
    """기업명·별칭·제품·위험 검색어를 중복 없이 만들고 수동 요청만 선택적으로 제한한다."""
    candidates = [company.name] if include_company_query else []
    for keyword in keywords:
        if keyword.keyword_type in {"alias", "product", "risk"}:
            candidates.append(f'"{company.name}" {keyword.value}')
            # 고유 별칭·제품·브랜드는 기업명이 없는 기사도 찾되, 일반적인 위험 표현은
            # 다른 기업의 기사가 대량 유입되지 않도록 반드시 기업명과 함께 검색한다.
            if keyword.keyword_type in {"alias", "product"}:
                candidates.append(keyword.value)
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        normalized = " ".join(item.split())
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result[:limit] if limit is not None else result


def completed_window_start(value: datetime, minutes: int = 15) -> datetime:
    """Return the start of the most recently completed aligned interval."""
    return floor_window(value, minutes) - timedelta(minutes=minutes)


def query_kind_for(
    query: str,
    company: Company,
    keywords: list[CompanyKeyword],
) -> str:
    """Record whether a hit came from the company, alias, product/brand or risk term."""
    normalized = " ".join(query.split()).casefold()
    if normalized == " ".join(company.name.split()).casefold():
        return "company"
    for keyword in keywords:
        if keyword.keyword_type not in {"alias", "product", "risk"}:
            continue
        value = " ".join(keyword.value.split())
        candidates = {value.casefold(), f'"{company.name}" {value}'.casefold()}
        if normalized in candidates:
            return keyword.keyword_type
    return "company"


def _collectors(settings: Settings, sources: list[str]) -> tuple[list, list[dict]]:
    """요청한 소스별 수집기를 만들고 설정이 부족한 소스는 오류로 기록한다."""
    collectors = []
    errors: list[dict] = []
    if "naver_api_hub" in sources:
        if settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret:
            collectors.append(
                NaverNewsCollector(
                    settings.naver_api_hub_client_id,
                    settings.naver_api_hub_client_secret,
                )
            )
        else:
            errors.append({"source": "naver_api_hub", "message": "NAVER API HUB 키가 설정되지 않았습니다."})
    if "kakao_daum" in sources:
        if settings.kakao_rest_api_key:
            collectors.append(KakaoDaumSearchCollector(settings.kakao_rest_api_key))
        else:
            errors.append({"source": "kakao_daum", "message": "KAKAO_REST_API_KEY가 설정되지 않았습니다."})
    if "tavily" in sources:
        if settings.tavily_api_key:
            collectors.append(TavilyNewsCollector(settings.tavily_api_key))
        else:
            errors.append({"source": "tavily", "message": "TAVILY_API_KEY가 설정되지 않았습니다."})
    if "youtube_comment" in sources:
        if settings.youtube_api_key:
            collectors.append(YouTubeCommentCollector(settings.youtube_api_key))
        else:
            errors.append({"source": "youtube_comment", "message": "YOUTUBE_API_KEY가 설정되지 않았습니다."})
    return collectors, errors


def run_collection(
    company_id: int,
    job_type: str,
    requested_from: datetime,
    requested_to: datetime | None = None,
    keyword_ids: list[int] | None = None,
    sources: list[str] | None = None,
    max_queries: int | None = None,
    scheduled_for: datetime | None = None,
    attempt_number: int = 0,
    manage_incidents: bool = True,
    dispatch_notifications_after: bool = True,
) -> CollectionJob:
    """검색, 원문 저장, 필터링, 기업 연결, 감성·이상 분석까지 수집 파이프라인을 실행한다."""
    settings = get_settings()
    requested_to = requested_to or datetime.now(timezone.utc)
    sources = _canonical_sources(sources or ["naver_api_hub", "tavily"])
    # Tavily는 플랜 사용량과 무관하게 정기 실시간 수집 및 장애 재시도에서 제외한다.
    # 과거 백필과 사용자가 직접 선택한 수동 수집에서는 계속 사용할 수 있다.
    if job_type == "realtime":
        sources = [source for source in sources if source != "tavily"]
        if not sources:
            sources = _realtime_sources(settings) or ["naver_api_hub"]
    if scheduled_for is None and job_type == "realtime":
        scheduled_for = completed_window_start(
            requested_to,
            settings.collection_window_minutes,
        )
    else:
        scheduled_for = floor_window(
            scheduled_for or requested_to,
            settings.collection_window_minutes,
        )
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            raise ValueError("기업을 찾을 수 없습니다.")
        # 같은 기업의 수집이 스케줄러와 재시도·수동 실행에서 겹쳐도 순차 실행되도록 잠근다.
        _lock_company_collection(db, company_id)

        # 키워드 백필은 새 키워드만, 일반 수집은 기업의 전체 키워드를 사용한다.
        keyword_query = select(CompanyKeyword).where(CompanyKeyword.company_id == company_id)
        if keyword_ids is not None:
            keyword_query = keyword_query.where(CompanyKeyword.id.in_(keyword_ids))
        keywords = list(db.scalars(keyword_query.order_by(CompanyKeyword.id)))
        queries = build_queries(
            company,
            keywords,
            max_queries,
            include_company_query=keyword_ids is None,
        )
        # 이후 기사 연결에 사용할 작업 ID를 확보하도록 외부 API 호출 전에 실행 레코드를 만든다.
        job = CollectionJob(
            user_id=company.user_id,
            company_id=company_id,
            status="running",
            job_type=job_type,
            sources=sources,
            requested_from=requested_from,
            requested_to=requested_to,
        )
        db.add(job)
        db.flush()

        collectors, errors = _collectors(settings, sources)
        attempt_started_at = datetime.now(timezone.utc)
        source_stats: dict[str, dict] = {
            source: {
                "query_count": 0,
                "successful_query_count": 0,
                "fetched_count": 0,
                "errors": [],
                "started_at": attempt_started_at,
            }
            for source in sources
        }
        # 자격 증명 누락 등 수집기 생성 단계 오류도 제공자 실패로 남긴다.
        for item in errors:
            source_stats.setdefault(item.get("source", "unknown"), {}).setdefault("errors", []).append(
                item.get("message") or "collector configuration error"
            )
        existing_match_ids = set(
            db.scalars(
                select(CompanyArticleMatch.article_id).where(
                    CompanyArticleMatch.company_id == company_id
                )
            )
        )
        fetched_count = 0
        new_count = 0
        matched_count = 0
        new_match_ids: list[int] = []
        attempted_count = 0
        successful_queries = 0
        filter_config = _article_filter_config(settings)
        semantic_scorer = get_semantic_scorer(filter_config)

        # 제공자와 검색어의 모든 조합을 실행하되 한 요청의 실패가 나머지를 막지 않게 한다.
        # 유튜브는 검색 1회가 100유닛이라, 반복되는 실시간 수집에서는 회사당 대표 검색어만 사용해 쿼터를 아낀다.
        # 등록 시 1회뿐인 백필·수동 수집은 전체 검색어를 그대로 사용한다.
        for collector in collectors:
            collector_queries = queries
            if collector.source == "youtube_comment" and job_type == "realtime":
                collector_queries = queries[: max(1, settings.youtube_max_queries_per_run)]
            for query in collector_queries:
                attempted_count += 1
                source_stats[collector.source]["query_count"] += 1
                try:
                    collected = collector.search(query, requested_from.date())
                    successful_queries += 1
                    source_stats[collector.source]["successful_query_count"] += 1
                except Exception as exc:
                    # 개별 제공자 SDK의 예외 형식이 달라도 다른 수집원 실행은 계속한다.
                    source_stats[collector.source]["errors"].append(str(exc))
                    errors.append(
                        {
                            "source": collector.source,
                            "query": query,
                            "message": str(exc)[:300],
                        }
                    )
                    continue
                fetched_count += len(collected)
                source_stats[collector.source]["fetched_count"] += len(collected)
                for item in collected:
                    if item.published_at and item.published_at < requested_from:
                        continue
                    # 제공자 원문은 판정 결과와 무관하게 정규화된 형태로 먼저 보존한다.
                    normalized_url = normalize_url(item.url)
                    item_content_hash = content_hash(item.title, item.summary)
                    _lock_normalized_url(db, normalized_url)
                    raw = _raw_for_content(
                        db,
                        item.source,
                        normalized_url,
                        item_content_hash,
                    )
                    if raw is None:
                        raw = RawNewsArticle(
                            source=item.source,
                            title=item.title,
                            summary=item.summary,
                            url=item.url,
                            original_url=item.original_url,
                            normalized_url=normalized_url,
                            content_hash=item_content_hash,
                            published_at=item.published_at,
                            raw_payload=item.raw_payload or {},
                        )
                        db.add(raw)
                        db.flush()
                    else:
                        # 같은 내용의 재수집은 별도 원문으로 늘리지 않고 표현만 더 충실하게 보강한다.
                        if len(item.summary or "") > len(raw.summary or ""):
                            raw.summary = item.summary
                        if len(item.title or "") > len(raw.title or ""):
                            raw.title = item.title
                        raw.published_at = raw.published_at or item.published_at

                    query_hit = db.scalar(
                        select(ArticleQueryHit).where(
                            ArticleQueryHit.raw_article_id == raw.id,
                            ArticleQueryHit.company_id == company_id,
                            ArticleQueryHit.source == collector.source,
                            ArticleQueryHit.query == query,
                        )
                    )
                    if query_hit is None:
                        db.add(
                            ArticleQueryHit(
                                raw_article_id=raw.id,
                                company_id=company_id,
                                job_id=job.id,
                                source=collector.source,
                                query=query,
                                query_kind=query_kind_for(query, company, keywords),
                                matched_keyword=item.matched_keyword,
                            )
                        )
                    else:
                        query_hit.hit_count += 1
                        query_hit.job_id = job.id
                        query_hit.last_seen_at = datetime.now(timezone.utc)

                    # 같은 필터 버전으로 이미 판정한 원문은 재추론하지 않고 결과를 재사용한다.
                    filter_result = db.scalar(
                        select(ArticleFilterResult).where(
                            ArticleFilterResult.raw_article_id == raw.id,
                            ArticleFilterResult.company_id == company_id,
                            ArticleFilterResult.filter_version == filter_config.version,
                        )
                    )
                    article = None
                    if filter_result is None:
                        decision = classify_article(
                            company,
                            keywords,
                            item,
                            raw,
                            candidate_articles=_raw_candidates(db, raw),
                            semantic_scorer=semantic_scorer,
                            config=filter_config,
                        )
                        article = _curated_for_raw(db, decision.duplicate_of_raw_id)

                        # 중복 기사라도 이 기업에는 처음 연결되는 관련 기사라면 기존 정제 기사에 병합한다.
                        if (
                            decision.reason == "duplicate"
                            and article is not None
                            and decision.relevance_score
                            >= filter_config.relevance_accept_threshold
                            and decision.advertising_score
                            < filter_config.advertising_review_threshold
                        ):
                            decision.decision = "accepted"
                            decision.details["duplicate_merged"] = True

                        if decision.decision == "accepted" and article is None:
                            article, article_created = _get_or_create_curated_article(
                                db,
                                raw,
                                item,
                                normalized_url,
                            )
                            if article_created:
                                new_count += 1
                            else:
                                # 동일 원문 재사용은 자기 중복이 아니며, 다른 원문만 정제 URL 중복으로 연결한다.
                                _reuse_existing_curated_article(decision, raw, article)

                        filter_result = ArticleFilterResult(
                            raw_article_id=raw.id,
                            company_id=company_id,
                            decision=decision.decision,
                            reason=decision.reason,
                            duplicate_of_raw_id=decision.duplicate_of_raw_id,
                            curated_article_id=article.id if article else None,
                            relevance_score=decision.relevance_score,
                            advertising_score=decision.advertising_score,
                            confidence=decision.confidence,
                            classifier_kind=decision.classifier_kind,
                            filter_version=decision.filter_version,
                            details={
                                **decision.details,
                                "duplicate_score": decision.duplicate_score,
                                "matched_keyword": item.matched_keyword,
                            },
                        )
                        db.add(filter_result)
                        db.flush()
                    elif filter_result.decision == "accepted":
                        article = (
                            db.get(NewsArticle, filter_result.curated_article_id)
                            if filter_result.curated_article_id
                            else _curated_for_raw(db, raw.id)
                        )

                    if article is not None:
                        assign_story_cluster(
                            db,
                            article,
                            settings,
                            company_id=company_id,
                            semantic_scorer=semantic_scorer,
                        )

                    # 정제 기사 하나는 기업마다 한 번만 연결해 집계와 분석의 중복을 막는다.
                    if article is not None and article.id not in existing_match_ids:
                        db.add(
                            CompanyArticleMatch(
                                company_id=company_id,
                                article_id=article.id,
                                job_id=job.id,
                                matched_keyword=item.matched_keyword,
                            )
                        )
                        existing_match_ids.add(article.id)
                        new_match_ids.append(article.id)
                        matched_count += 1

        # 제공자별 시도를 먼저 고정해 유효한 빈 결과와 수집 장애를 구분한다.
        completed_at = datetime.now(timezone.utc)
        for stats in source_stats.values():
            stats["completed_at"] = completed_at
        attempts = record_attempts(
            db,
            job,
            source_stats,
            scheduled_for,
            attempt_number,
        )
        data_quality, _incident_id = evaluate_attempts(
            db,
            attempts,
            scheduled_for,
            settings,
            manage_incidents=manage_incidents and job_type == "realtime",
        )

        # 부분 성공을 구분해 운영 화면에서 제공자 오류와 수집 성과를 함께 확인하게 한다.
        job.query_count = attempted_count
        job.fetched_count = fetched_count
        job.new_count = new_count
        job.matched_count = matched_count
        job.errors = errors
        job.completed_at = completed_at
        if data_quality == "partial":
            job.status = "partial"
        elif data_quality == "unavailable":
            job.status = "failed"
        else:
            job.status = "completed"
        if data_quality != "unavailable":
            company.last_collected_at = requested_to
        if job_type == "realtime" and attempt_number == 0:
            # `scheduled_for` is the start of the interval that just ended.
            company.next_collection_at = scheduled_for + timedelta(
                minutes=2 * settings.collection_window_minutes
            )
        db.commit()
        db.refresh(job)

    # 수집 트랜잭션을 끝낸 뒤 후속 감성 분석과 15분 특징 생성을 별도 세션에서 수행한다.
    if matched_count:
        analyze_company_articles(company_id)
        enqueue_llm_labeling_for_company(company_id)
        enqueue_company_risk_articles(company_id, new_match_ids)
    if job_type == "realtime":
        build_feature_window(
            company_id,
            scheduled_for,
            data_quality,
            [item.source for item in attempts if item.status == "succeeded"],
            [item.source for item in attempts if item.status == "failed"],
            update_events=not settings.story_risk_engine_enabled,
            generate_response_drafts=not settings.story_risk_engine_enabled,
        )
        if settings.story_risk_engine_enabled:
            close_stale_story_events(company_id)
    if dispatch_notifications_after:
        dispatch_pending_notifications(settings)
    return job


def reanalyze_existing_data(user_id: int) -> dict[str, int | str]:
    """Run the four production analysis stages over already stored user data."""
    settings = get_settings()
    filter_config = _article_filter_config(settings)
    semantic_scorer = get_semantic_scorer(filter_config)
    counters = {
        "filter_evaluated": 0,
        "filter_accepted": 0,
        "filter_rejected": 0,
        "filter_review_required": 0,
        "article_matches_added": 0,
        "article_matches_removed": 0,
        "risk_assessments_removed": 0,
        "sentiment_queued": 0,
        "sentiment_analyzed": 0,
        "feature_windows": 0,
        "risk_scored_windows": 0,
    }
    with SessionLocal() as db:
        company_ids = list(
            db.scalars(
                select(Company.id)
                .where(Company.user_id == user_id)
                .order_by(Company.id)
            )
        )

    for company_id in company_ids:
        with SessionLocal() as db:
            company = db.get(Company, company_id)
            if company is None:
                continue
            keywords = list(
                db.scalars(
                    select(CompanyKeyword)
                    .where(CompanyKeyword.company_id == company_id)
                    .order_by(CompanyKeyword.id)
                )
            )
            hit_rows = list(
                db.execute(
                    select(RawNewsArticle, ArticleQueryHit)
                    .join(
                        ArticleQueryHit,
                        ArticleQueryHit.raw_article_id == RawNewsArticle.id,
                    )
                    .where(ArticleQueryHit.company_id == company_id)
                    .order_by(
                        RawNewsArticle.id,
                        ArticleQueryHit.last_seen_at.desc(),
                        ArticleQueryHit.id.desc(),
                    )
                )
            )
            latest_hits: dict[int, tuple[RawNewsArticle, ArticleQueryHit]] = {}
            for raw, hit in hit_rows:
                latest_hits.setdefault(raw.id, (raw, hit))

            rows_to_evaluate = list(latest_hits.values())
            normalized_urls = {
                raw.normalized_url for raw, _hit in rows_to_evaluate
                if raw.normalized_url
            }
            same_url_candidates: dict[str, list[RawNewsArticle]] = {
                url: [] for url in normalized_urls
            }
            if normalized_urls:
                for candidate in db.scalars(
                    select(RawNewsArticle)
                    .where(RawNewsArticle.normalized_url.in_(normalized_urls))
                    .order_by(RawNewsArticle.collected_at.desc())
                ):
                    same_url_candidates[candidate.normalized_url].append(candidate)
            # 재정제에서도 실시간 수집과 같은 "동일 제목 + 15분" 규칙을 쓴다.
            # 낮은 raw ID를 기준 기사로 고정해 두 원문이 서로를 중복으로 가리키는
            # 순환 참조를 만들지 않는다.
            same_title_candidates: dict[str, list[RawNewsArticle]] = {}
            raw_titles = {
                candidate.title for candidate, _hit in rows_to_evaluate
                if candidate.title
            }
            if raw_titles:
                # 기준 원문이 다른 기업의 검색 결과로 먼저 저장됐더라도 찾을 수 있게
                # 회사별 hit 범위가 아니라 전체 원문에서 같은 제목을 조회한다.
                for candidate in db.scalars(
                    select(RawNewsArticle)
                    .where(RawNewsArticle.title.in_(raw_titles))
                    .order_by(RawNewsArticle.id)
                ):
                    title_key = normalize_text(candidate.title)
                    if title_key:
                        same_title_candidates.setdefault(title_key, []).append(candidate)
            existing_filter_results = {
                result.raw_article_id: result
                for result in db.scalars(
                    select(ArticleFilterResult).where(
                        ArticleFilterResult.company_id == company_id,
                        ArticleFilterResult.filter_version == filter_config.version,
                        ArticleFilterResult.raw_article_id.in_(latest_hits),
                    )
                )
            } if latest_hits else {}
            article_texts = [normalized_content(raw) for raw, _hit in rows_to_evaluate]
            aliases = [
                keyword.value for keyword in keywords if keyword.keyword_type == "alias"
            ]
            products = [
                keyword.value for keyword in keywords if keyword.keyword_type == "product"
            ]
            reranker_predictions = predict_company_relevance_batch(
                [
                    (company.name, aliases, products, raw.title, raw.summary or "")
                    for raw, _hit in rows_to_evaluate
                ]
            )
            if rows_to_evaluate and all(
                prediction is not None for prediction in reranker_predictions
            ):
                # 승격된 공용 reranker가 전체 배치를 처리했으면 결과에 쓰이지 않는
                # 구형 관련성 모델 두 개를 다시 실행하지 않는다.
                relevance_predictions = [None] * len(article_texts)
                topical_predictions = [None] * len(article_texts)
            else:
                relevance_predictions = predict_relevance_batch(
                    [(company.name, text) for text in article_texts]
                )
                topical_predictions = (
                    predict_topical_relevance_batch(article_texts)
                    if company.name in TOPICAL_RELEVANCE_TRAINED_COMPANIES
                    else [None] * len(article_texts)
                )

            accepted_articles: dict[int, ArticleQueryHit] = {}
            for (raw, hit), reranker_prediction, relevance_prediction, topical_prediction in zip(
                rows_to_evaluate,
                reranker_predictions,
                relevance_predictions,
                topical_predictions,
            ):
                decision = classify_article(
                    company,
                    keywords,
                    raw,
                    raw,
                    candidate_articles=list({
                        candidate.id: candidate
                        for candidate in [
                            *same_url_candidates.get(raw.normalized_url, []),
                            *same_title_candidates.get(normalize_text(raw.title), []),
                        ]
                        if candidate.id != raw.id
                        and candidate.id < raw.id
                    }.values())[:250],
                    semantic_scorer=semantic_scorer,
                    config=filter_config,
                    precomputed_company_reranker=reranker_prediction,
                    precomputed_relevance=relevance_prediction,
                    precomputed_topical_relevance=topical_prediction,
                )
                article = _curated_for_raw(db, decision.duplicate_of_raw_id)
                if (
                    decision.reason == "duplicate"
                    and article is not None
                    and decision.relevance_score
                    >= filter_config.relevance_accept_threshold
                    and decision.advertising_score
                    < filter_config.advertising_review_threshold
                ):
                    decision.decision = "accepted"
                    decision.details["duplicate_merged"] = True
                if decision.decision == "accepted" and article is None:
                    article, _created = _get_or_create_curated_article(
                        db,
                        raw,
                        raw,
                        raw.normalized_url,
                    )
                    if not _created:
                        _reuse_existing_curated_article(decision, raw, article)

                result = existing_filter_results.get(raw.id)
                if result is None:
                    result = ArticleFilterResult(
                        raw_article_id=raw.id,
                        company_id=company_id,
                        decision=decision.decision,
                        reason=decision.reason,
                        classifier_kind=decision.classifier_kind,
                        filter_version=decision.filter_version,
                    )
                    db.add(result)
                    existing_filter_results[raw.id] = result
                result.decision = decision.decision
                result.reason = decision.reason
                result.duplicate_of_raw_id = decision.duplicate_of_raw_id
                result.curated_article_id = (
                    article.id
                    if article is not None and decision.decision == "accepted"
                    else None
                )
                result.relevance_score = decision.relevance_score
                result.advertising_score = decision.advertising_score
                result.confidence = decision.confidence
                result.classifier_kind = decision.classifier_kind
                result.details = {
                    **decision.details,
                    "duplicate_score": decision.duplicate_score,
                    "matched_keyword": hit.matched_keyword,
                    "reanalyzed_at": datetime.now(timezone.utc).isoformat(),
                }
                result.filtered_at = datetime.now(timezone.utc)
                counters["filter_evaluated"] += 1
                counters[f"filter_{decision.decision}"] += 1
                if article is not None:
                    if decision.decision == "accepted":
                        accepted_articles[article.id] = hit
                        assign_story_cluster(
                            db,
                            article,
                            settings,
                            company_id=company_id,
                            semantic_scorer=semantic_scorer,
                        )

            current_matches = {
                match.article_id: match
                for match in db.scalars(
                    select(CompanyArticleMatch).where(
                        CompanyArticleMatch.company_id == company_id
                    )
                )
            }
            for article_id, hit in accepted_articles.items():
                if article_id not in current_matches:
                    db.add(
                        CompanyArticleMatch(
                            company_id=company_id,
                            article_id=article_id,
                            job_id=hit.job_id,
                            matched_keyword=hit.matched_keyword,
                        )
                    )
                    counters["article_matches_added"] += 1
            # This is a full historical pass, so the accepted set is authoritative
            # for the company.  A previously interrupted pass may already have
            # cleared ArticleFilterResult.curated_article_id while leaving the old
            # CompanyArticleMatch behind; limiting removal to result pointers would
            # make that stale match impossible to discover on the next run.
            stale_article_ids = set(current_matches) - set(accepted_articles)
            stale_assessment_ids = set(
                db.scalars(
                    select(ArticleRiskAssessment.article_id).where(
                        ArticleRiskAssessment.company_id == company_id,
                    )
                )
            ) - set(accepted_articles)
            if stale_assessment_ids:
                db.execute(
                    delete(ArticleRiskAssessment).where(
                        ArticleRiskAssessment.company_id == company_id,
                        ArticleRiskAssessment.article_id.in_(stale_assessment_ids),
                    )
                )
                counters["risk_assessments_removed"] += len(stale_assessment_ids)
            for article_id in stale_article_ids:
                match = current_matches.get(article_id)
                if match is not None:
                    db.delete(match)
                    counters["article_matches_removed"] += 1
            db.commit()

    sentiment_path = Path(settings.pretrained_sentiment_model_path).expanduser()
    target_sentiment_model = (
        f"local:{sentiment_path.name}" if sentiment_path.is_dir() else None
    )
    if target_sentiment_model is not None and company_ids:
        with SessionLocal() as db:
            article_ids = list(
                db.scalars(
                    select(CompanyArticleMatch.article_id)
                    .where(CompanyArticleMatch.company_id.in_(company_ids))
                    .distinct()
                )
            )
            articles = list(
                db.scalars(
                    select(NewsArticle).where(NewsArticle.id.in_(article_ids))
                )
            )
            for article in articles:
                if article.sentiment_model != target_sentiment_model:
                    article.sentiment_label = None
                    article.sentiment_score = None
                    article.sentiment_confidence = None
                    article.positive_probability = None
                    article.neutral_probability = None
                    article.negative_probability = None
                    article.sentiment_model = None
                    article.analyzed_at = None
                    counters["sentiment_queued"] += 1
            db.commit()
        for company_id in company_ids:
            while True:
                analyzed = analyze_company_articles(company_id, batch_limit=100)
                counters["sentiment_analyzed"] += analyzed
                if analyzed < 100:
                    break

    risk_counts = reanalyze_historical_windows(user_id=user_id)
    counters.update(risk_counts)
    if settings.story_risk_engine_enabled:
        for target_company_id in company_ids:
            # 과거 재분석은 위험 결과까지만 갱신한다. 대응 초안은 별도 화면에서
            # 필요할 때 생성해 대량 API 호출과 토큰 사용을 피한다.
            story_counts = process_company_risk_articles(
                target_company_id,
                enqueue_drafts=False,
            )
            counters["story_articles_assessed"] = counters.get("story_articles_assessed", 0) + story_counts["assessed"]
            counters["story_events_changed"] = counters.get("story_events_changed", 0) + story_counts["events_changed"]
    return {"status": "completed", **counters}


def existing_data_reanalysis_status(user_id: int) -> dict[str, int | str]:
    """Return process-local progress for the current user's background run."""
    with _reanalysis_lock:
        return dict(
            _reanalysis_statuses.get(
                user_id,
                {"status": "idle", "message": "실행 대기 중입니다."},
            )
        )


def start_existing_data_reanalysis(user_id: int) -> dict[str, int | str]:
    """Start one non-blocking historical reanalysis per user."""
    with _reanalysis_lock:
        current = _reanalysis_statuses.get(user_id)
        if current is not None and current.get("status") == "running":
            return dict(current)
        _reanalysis_statuses[user_id] = {
            "status": "running",
            "message": "기존 데이터를 백그라운드에서 재평가하고 있습니다.",
        }

    def run() -> None:
        try:
            result = reanalyze_existing_data(user_id)
            result["message"] = "기존 데이터 재평가가 완료되었습니다."
        except Exception as exc:
            logger.exception("Existing-data reanalysis failed for user %s", user_id)
            result = {
                "status": "failed",
                "message": f"기존 데이터 재평가 실패: {exc}"[:500],
            }
        with _reanalysis_lock:
            _reanalysis_statuses[user_id] = result

    _reanalysis_executor.submit(run)
    return existing_data_reanalysis_status(user_id)


def initialize_company_monitoring(
    company_id: int,
    is_new: bool,
    added_keyword_ids: list[int],
) -> None:
    """신규 기업 또는 추가 키워드의 백필을 수행하고 실시간 모니터링을 시작한다."""
    try:
        settings = get_settings()
        monitoring_paused = False
        with SessionLocal() as db:
            company = db.get(Company, company_id)
            if company is None:
                return
            monitoring_paused = company.monitoring_status == "paused"
            started_at = company.monitoring_started_at or datetime.now(timezone.utc)
            company.monitoring_started_at = started_at
            if is_new:
                company.analysis_status = "warming"
                company.next_collection_at = started_at + timedelta(
                    seconds=settings.realtime_interval_seconds
                )
            db.commit()

        if monitoring_paused:
            return

        if is_new:
            run_collection(
                company_id,
                "backfill",
                started_at - timedelta(days=7),
                requested_to=started_at,
                sources=_backfill_sources(settings),
            )
        elif added_keyword_ids:
            run_collection(
                company_id,
                "keyword_backfill",
                datetime.now(timezone.utc) - timedelta(days=7),
                keyword_ids=added_keyword_ids,
                sources=_backfill_sources(settings),
            )

        realtime_sources = _realtime_sources(settings) or ["naver"]
        run_collection(
            company_id,
            "realtime",
            started_at - timedelta(minutes=settings.realtime_overlap_minutes),
            sources=realtime_sources,
        )
    except Exception:
        logger.exception("Failed to initialize monitoring for company %s", company_id)
        with SessionLocal() as db:
            company = db.get(Company, company_id)
            if company is not None:
                company.monitoring_status = "error"
                company.analysis_status = "error"
                company.analysis_error = "모니터링 초기화 중 오류가 발생했습니다. 백엔드 로그를 확인하세요."
                db.commit()


def refresh_company_monitoring(
    company_id: int,
    company_name_changed: bool,
    added_keyword_ids: list[int],
) -> None:
    """수정된 기업명 또는 새 키워드에 해당하는 최근 7일 자료를 보강 수집한다."""
    try:
        settings = get_settings()
        requested_to = datetime.now(timezone.utc)
        if company_name_changed:
            run_collection(
                company_id,
                "backfill",
                requested_to - timedelta(days=7),
                requested_to=requested_to,
                sources=_backfill_sources(settings),
            )
            backfill_historical_windows(company_id)
        elif added_keyword_ids:
            run_collection(
                company_id,
                "keyword_backfill",
                requested_to - timedelta(days=7),
                requested_to=requested_to,
                keyword_ids=added_keyword_ids,
                sources=_backfill_sources(settings),
            )
    except Exception:
        logger.exception("Failed to refresh monitoring for company %s", company_id)


def run_realtime_tick() -> None:
    """수집 예정 시간이 지난 활성 기업들을 찾아 실시간 수집을 한 차례 실행한다."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        company_ids = list(
            db.scalars(
                select(Company.id).where(
                    Company.monitoring_status.in_(["backfilling", "warming", "active", "error"]),
                    or_(Company.next_collection_at.is_(None), Company.next_collection_at <= now),
                )
            )
        )
    # 수집원이 하나도 구성되지 않은 상태도 조용히 건너뛰지 않고 운영 장애로 기록한다.
    realtime_sources = _realtime_sources(settings) or ["naver_api_hub"]
    scheduled_for = completed_window_start(now, settings.collection_window_minutes)
    realtime_sources = _throttle_youtube_for_tick(realtime_sources, scheduled_for, settings)
    for company_id in company_ids:
        cursor = now - timedelta(minutes=settings.realtime_overlap_minutes)
        try:
            with SessionLocal() as db:
                company = db.get(Company, company_id)
                if company is None:
                    continue
                cursor = company.last_collected_at or company.monitoring_started_at
                if cursor is None:
                    cursor = datetime.now(timezone.utc) - timedelta(days=company.backfill_days)
            run_collection(
                company_id,
                "realtime",
                cursor - timedelta(minutes=settings.realtime_overlap_minutes),
                requested_to=now,
                sources=realtime_sources,
                scheduled_for=scheduled_for,
                dispatch_notifications_after=False,
            )
        except Exception as exc:
            logger.exception("Realtime monitoring failed for company %s", company_id)
            try:
                record_pipeline_failure(
                    company_id,
                    scheduled_for,
                    f"{type(exc).__name__}: {exc}",
                    requested_from=cursor - timedelta(minutes=settings.realtime_overlap_minutes),
                    requested_to=now,
                    settings=settings,
                    dispatch=False,
                )
            except Exception:
                logger.exception("Failed to persist realtime pipeline incident for company %s", company_id)

    run_due_collection_retries()
    if settings.story_risk_engine_enabled:
        close_stale_story_events(now=now)
    dispatch_pending_notifications(settings)
    _run_due_daily_model_check(now)


def _run_due_daily_model_check(now: datetime) -> None:
    """Persist one model-operation report per Seoul day without affecting collection."""
    global _last_model_operation_check_date
    check_date = now.astimezone(SEOUL).date()
    if _last_model_operation_check_date == check_date:
        return
    try:
        with SessionLocal() as db:
            ensure_daily_model_check(db, now=now)
        _last_model_operation_check_date = check_date
    except Exception:
        logger.exception("Daily model-operation check failed")


def run_due_collection_retries() -> int:
    """Retry aggregated all-source incidents at 1, 5 and 15 minute intervals."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        incidents = list(
            db.scalars(
                select(CollectionIncident).where(
                    CollectionIncident.status == "retrying",
                    CollectionIncident.data_quality == "unavailable",
                    CollectionIncident.next_retry_at.is_not(None),
                    CollectionIncident.next_retry_at <= now,
                )
            )
        )
        retry_items: list[tuple[CollectionIncident, list[int], list[int]]] = []
        for incident in incidents:
            owned_company_ids: list[int] = []
            skipped_company_ids: list[int] = []
            for company_id in dict.fromkeys(incident.affected_company_ids or []):
                company = db.get(Company, company_id)
                if company is None or company.user_id != incident.user_id:
                    skipped_company_ids.append(company_id)
                    continue
                owned_company_ids.append(company_id)
            retry_items.append((incident, owned_company_ids, skipped_company_ids))
    retried = 0
    for incident, company_ids, skipped_company_ids in retry_items:
        succeeded: list[int] = []
        retry_sources = _incident_retry_sources(
            settings,
            list(incident.sources or []),
        )
        retry_sources = _throttle_youtube_for_tick(retry_sources, incident.scheduled_for, settings)
        for company_id in company_ids:
            try:
                job = run_collection(
                    company_id,
                    "realtime",
                    incident.scheduled_for
                    - timedelta(minutes=settings.realtime_overlap_minutes),
                    requested_to=now,
                    sources=retry_sources,
                    scheduled_for=incident.scheduled_for,
                    attempt_number=incident.retry_count + 1,
                    manage_incidents=False,
                    dispatch_notifications_after=False,
                )
                if job.status == "completed":
                    succeeded.append(company_id)
            except Exception:
                logger.exception(
                    "Collection retry failed for incident %s company %s",
                    incident.id,
                    company_id,
                )
            retried += 1
        # 삭제된 기업이나 다른 사용자 소유 ID는 수집하지 않고 장애 목록에서도 제거한다.
        complete_retry(
            incident.id,
            [*succeeded, *skipped_company_ids],
            settings,
        )
    dispatch_pending_notifications(settings)
    return retried


async def realtime_monitoring_loop(stop_event: asyncio.Event) -> None:
    """종료 신호가 올 때까지 주기적으로 실시간 수집 틱을 백그라운드에서 실행한다."""
    settings = get_settings()
    schedule_check_seconds = min(5, max(1, settings.realtime_interval_seconds))
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=schedule_check_seconds)
        except asyncio.TimeoutError:
            await asyncio.to_thread(run_realtime_tick)
