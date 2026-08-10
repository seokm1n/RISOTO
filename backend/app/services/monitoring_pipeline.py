import asyncio
from datetime import datetime, timedelta, timezone
import logging

import httpx
from sqlalchemy import select, text

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import CollectionJob, Company, CompanyArticleMatch, CompanyKeyword, NewsArticle
from app.services.anomaly_detection import fit_or_score_company
from app.services.news_collectors import NaverNewsCollector, TavilyNewsCollector
from app.services.sentiment import analyze_company_articles


logger = logging.getLogger(__name__)


def _realtime_sources(settings: Settings) -> list[str]:
    if settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret:
        return ["naver"]
    if settings.tavily_api_key:
        return ["tavily"]
    return []


def build_queries(
    company: Company,
    keywords: list[CompanyKeyword],
    limit: int,
    include_company_query: bool = True,
) -> list[str]:
    candidates = [company.name] if include_company_query else []
    for keyword in keywords:
        if keyword.keyword_type in {"alias", "peer", "product", "risk"}:
            candidates.append(f'"{company.name}" {keyword.value}')
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        normalized = " ".join(item.split())
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result[:limit]


def _collectors(settings: Settings, sources: list[str]) -> tuple[list, list[dict]]:
    collectors = []
    errors: list[dict] = []
    if "naver" in sources:
        if settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret:
            collectors.append(
                NaverNewsCollector(
                    settings.naver_api_hub_client_id,
                    settings.naver_api_hub_client_secret,
                )
            )
        else:
            errors.append({"source": "naver", "message": "NAVER API HUB 키가 설정되지 않았습니다."})
    if "tavily" in sources:
        if settings.tavily_api_key:
            collectors.append(TavilyNewsCollector(settings.tavily_api_key))
        else:
            errors.append({"source": "tavily", "message": "TAVILY_API_KEY가 설정되지 않았습니다."})
    return collectors, errors


def run_collection(
    company_id: int,
    job_type: str,
    requested_from: datetime,
    requested_to: datetime | None = None,
    keyword_ids: list[int] | None = None,
    sources: list[str] | None = None,
    max_queries: int = 5,
) -> CollectionJob:
    settings = get_settings()
    requested_to = requested_to or datetime.now(timezone.utc)
    sources = sources or ["naver", "tavily"]
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            raise ValueError("기업을 찾을 수 없습니다.")

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
        job = CollectionJob(
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
        attempted_count = 0
        successful_queries = 0

        for collector in collectors:
            for query in queries:
                attempted_count += 1
                try:
                    collected = collector.search(query, requested_from.date())
                    successful_queries += 1
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(
                        {
                            "source": collector.source,
                            "query": query,
                            "message": str(exc)[:300],
                        }
                    )
                    continue
                fetched_count += len(collected)
                for item in collected:
                    if item.published_at and item.published_at < requested_from:
                        continue
                    article = db.scalar(select(NewsArticle).where(NewsArticle.url == item.url))
                    if article is None:
                        article = NewsArticle(
                            source=item.source,
                            title=item.title,
                            summary=item.summary,
                            url=item.url,
                            original_url=item.original_url,
                            published_at=item.published_at,
                            raw_payload=item.raw_payload,
                        )
                        db.add(article)
                        db.flush()
                        new_count += 1
                    if article.id not in existing_match_ids:
                        db.add(
                            CompanyArticleMatch(
                                company_id=company_id,
                                article_id=article.id,
                                job_id=job.id,
                                matched_keyword=item.matched_keyword,
                            )
                        )
                        existing_match_ids.add(article.id)
                        matched_count += 1

        job.query_count = attempted_count
        job.fetched_count = fetched_count
        job.new_count = new_count
        job.matched_count = matched_count
        job.errors = errors
        job.completed_at = datetime.now(timezone.utc)
        if errors and successful_queries:
            job.status = "partial"
        elif errors:
            job.status = "failed"
        else:
            job.status = "completed"
        if successful_queries:
            company.last_collected_at = requested_to
        db.commit()
        db.refresh(job)

    if matched_count:
        analyze_company_articles(company_id)
    fit_or_score_company(company_id)
    return job


def initialize_company_monitoring(
    company_id: int,
    is_new: bool,
    added_keyword_ids: list[int],
) -> None:
    try:
        settings = get_settings()
        with SessionLocal() as db:
            company = db.get(Company, company_id)
            if company is None:
                return
            started_at = company.monitoring_started_at or datetime.now(timezone.utc)
            company.monitoring_started_at = started_at
            if is_new:
                company.monitoring_status = "backfilling"
                company.analysis_status = "pending"
            db.commit()

        if is_new:
            run_collection(
                company_id,
                "backfill",
                started_at - timedelta(days=7),
                requested_to=started_at,
                sources=["naver", "tavily"],
            )
        elif added_keyword_ids:
            run_collection(
                company_id,
                "keyword_backfill",
                datetime.now(timezone.utc) - timedelta(days=7),
                keyword_ids=added_keyword_ids,
                sources=["naver", "tavily"],
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


def run_realtime_tick() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        company_ids = list(
            db.scalars(
                select(Company.id).where(
                    Company.monitoring_status.in_(["backfilling", "warming", "active", "error"])
                )
            )
        )
    realtime_sources = _realtime_sources(settings)
    if not realtime_sources:
        return
    for company_id in company_ids:
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
                sources=realtime_sources,
            )
        except Exception:
            logger.exception("Realtime monitoring failed for company %s", company_id)


async def realtime_monitoring_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.realtime_interval_seconds)
        except asyncio.TimeoutError:
            await asyncio.to_thread(run_realtime_tick)
