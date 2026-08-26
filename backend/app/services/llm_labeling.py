"""Automatic LLM-based article ground-truth labeling.

Instead of relying on a model's own predictions (which only entrenches its
existing blind spots) or requiring a human to review every collected
article, an independently-prompted LLM judges each new article's relevance,
advertisement and sentiment labels and writes them straight to
``article_labels`` as ``confirmed``. A human still cross-checks a small
random monthly sample (see ``audit_sample_candidates``) so systematic drift
in the LLM's own judgement does not go unnoticed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.config import get_settings
from app.database import SessionLocal
from app.models import ArticleFilterResult, ArticleLabel, Company, CompanyKeyword, RawNewsArticle
from app.services.review_identity import INTERNAL_REVIEW_ACTOR


logger = logging.getLogger(__name__)
_label_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-labeling")

SEOUL = ZoneInfo("Asia/Seoul")
LLM_ANNOTATOR_PREFIX = "llm:"
RELEVANCE_LABELS = ("relevant", "incidental", "irrelevant", "uncertain")
ADVERTISEMENT_LABELS = ("yes", "no", "uncertain")
SENTIMENT_LABELS = ("positive", "neutral", "negative", "mixed", "uncertain", "not_applicable")


def _label_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["relevance_label", "advertisement_label", "sentiment_label", "reason"],
        "properties": {
            "relevance_label": {"type": "string", "enum": list(RELEVANCE_LABELS)},
            "advertisement_label": {"type": "string", "enum": list(ADVERTISEMENT_LABELS)},
            "sentiment_label": {"type": "string", "enum": list(SENTIMENT_LABELS)},
            "reason": {"type": "string"},
        },
    }


def _company_context(db: Session, company: Company) -> dict:
    keywords = list(
        db.scalars(select(CompanyKeyword).where(CompanyKeyword.company_id == company.id))
    )
    return {
        "name": company.name,
        "aliases": [item.value for item in keywords if item.keyword_type == "alias"],
        "products": [item.value for item in keywords if item.keyword_type == "product"],
    }


def call_llm_label(company_context: dict, raw: RawNewsArticle, model_name: str) -> dict | None:
    """Ask the configured model to independently label one article; return None on any failure."""
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    instruction = (
        "다음 뉴스 기사가 지정된 기업 자체에 관한 내용인지, 광고·홍보성 문구인지, 어떤 감성 어조인지를 "
        "사람 검수자처럼 독립적으로 판단하라. 기업명이 같아도 스포츠팀, 인물, 다른 업종 등 동명이인을 "
        "다루는 기사면 relevance_label을 irrelevant로 표시하라. 기사가 무관하면 sentiment_label은 "
        "not_applicable로 하라. 판단 근거를 reason에 한두 문장으로 남겨라."
    )
    prompt = {
        "instruction": instruction,
        "company": company_context,
        "article": {
            "title": raw.title,
            "summary": raw.summary or "",
            "source": raw.source,
        },
    }
    try:
        from openai import OpenAI

        response = OpenAI(api_key=settings.openai_api_key).responses.create(
            model=model_name,
            input=json.dumps(prompt, ensure_ascii=False, default=str),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "article_llm_label_v1",
                    "strict": True,
                    "schema": _label_schema(),
                }
            },
        )
        payload = json.loads(response.output_text)
    except Exception:
        logger.exception("LLM article labeling call failed for raw_article_id=%s", raw.id)
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("relevance_label") not in RELEVANCE_LABELS
        or payload.get("advertisement_label") not in ADVERTISEMENT_LABELS
        or payload.get("sentiment_label") not in SENTIMENT_LABELS
    ):
        return None
    return payload


def _latest_filter_result_ids():
    return (
        select(func.max(ArticleFilterResult.id).label("id"))
        .group_by(ArticleFilterResult.company_id, ArticleFilterResult.raw_article_id)
        .subquery()
    )


def _unlabeled_query(company_id: int | None, limit: int):
    latest_ids = _latest_filter_result_ids()
    query = (
        select(ArticleFilterResult, RawNewsArticle, Company)
        .join(latest_ids, latest_ids.c.id == ArticleFilterResult.id)
        .join(RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id)
        .join(Company, Company.id == ArticleFilterResult.company_id)
        .where(
            ~exists(
                select(ArticleLabel.id).where(
                    ArticleLabel.company_id == ArticleFilterResult.company_id,
                    ArticleLabel.raw_article_id == ArticleFilterResult.raw_article_id,
                    ArticleLabel.status.in_(["confirmed", "adjudicated"]),
                )
            )
        )
        .order_by(ArticleFilterResult.filtered_at.desc())
        .limit(limit)
    )
    if company_id is not None:
        query = query.where(ArticleFilterResult.company_id == company_id)
    return query


def label_articles(db: Session, *, company_id: int | None, limit: int) -> dict:
    """Label up to `limit` not-yet-confirmed articles and persist them as confirmed labels."""
    settings = get_settings()
    model_name = settings.llm_labeling_model_name
    annotator = f"{LLM_ANNOTATOR_PREFIX}{model_name}"
    rows = db.execute(_unlabeled_query(company_id, limit)).all()

    labeled = 0
    failed = 0
    skipped = 0
    context_cache: dict[int, dict] = {}
    for _result, raw, company in rows:
        context = context_cache.get(company.id)
        if context is None:
            context = _company_context(db, company)
            context_cache[company.id] = context
        payload = call_llm_label(context, raw, model_name)
        if payload is None:
            failed += 1
            continue
        db.add(
            ArticleLabel(
                company_id=company.id,
                raw_article_id=raw.id,
                annotator=annotator,
                relevance_label=payload["relevance_label"],
                advertisement_label=payload["advertisement_label"],
                sentiment_label=payload["sentiment_label"],
                status="confirmed",
                notes=str(payload.get("reason", ""))[:4000],
            )
        )
        try:
            db.commit()
            labeled += 1
        except IntegrityError:
            # Another concurrent run already labeled this exact article/annotator pair.
            db.rollback()
            skipped += 1
    return {
        "candidates_considered": len(rows),
        "labeled": labeled,
        "failed": failed,
        "skipped": skipped,
        "model_name": model_name,
    }


def enqueue_llm_labeling_for_company(company_id: int) -> None:
    """Fire-and-forget LLM labeling for one company's newly matched articles.

    Runs after a collection tick, mirroring enqueue_response_draft: never
    delays or blocks ingestion, and failures stay visible only in logs.
    """
    settings = get_settings()
    if not settings.llm_labeling_enabled or not settings.openai_api_key:
        return

    def _run() -> None:
        try:
            with SessionLocal() as db:
                label_articles(db, company_id=company_id, limit=settings.llm_labeling_batch_size)
        except Exception:
            logger.exception("automatic LLM labeling failed for company_id=%s", company_id)

    _label_executor.submit(_run)


def run_llm_labeling_backlog(db: Session, limit: int | None = None) -> dict:
    """Manually catch up any articles the automatic per-company trigger missed."""
    settings = get_settings()
    return label_articles(db, company_id=None, limit=limit or settings.llm_labeling_batch_size)


def audit_sample_candidates(db: Session, user_id: int, limit: int) -> list:
    """Blind random sample of LLM-labeled articles a human hasn't cross-checked yet."""
    latest_ids = _latest_filter_result_ids()
    llm_label = aliased(ArticleLabel)
    human_label = aliased(ArticleLabel)
    query = (
        select(ArticleFilterResult, RawNewsArticle, Company)
        .join(latest_ids, latest_ids.c.id == ArticleFilterResult.id)
        .join(
            llm_label,
            and_(
                llm_label.company_id == ArticleFilterResult.company_id,
                llm_label.raw_article_id == ArticleFilterResult.raw_article_id,
                llm_label.annotator.like(f"{LLM_ANNOTATOR_PREFIX}%"),
                llm_label.status.in_(["confirmed", "adjudicated"]),
            ),
        )
        .join(RawNewsArticle, RawNewsArticle.id == ArticleFilterResult.raw_article_id)
        .join(Company, Company.id == ArticleFilterResult.company_id)
        .where(
            Company.user_id == user_id,
            ~exists(
                select(human_label.id).where(
                    human_label.company_id == ArticleFilterResult.company_id,
                    human_label.raw_article_id == ArticleFilterResult.raw_article_id,
                    human_label.annotator == INTERNAL_REVIEW_ACTOR,
                )
            ),
        )
        .order_by(func.random())
        .limit(limit)
    )
    return list(db.execute(query).all())


def _month_start(now: datetime) -> datetime:
    local = now.astimezone(SEOUL)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def llm_labeling_status(db: Session, *, now: datetime | None = None) -> dict:
    """Read-only dashboard summary: backlog, recent throughput and this month's audit agreement."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    day_ago = now - timedelta(hours=24)
    month_start = _month_start(now)

    llm_total = int(
        db.scalar(
            select(func.count(ArticleLabel.id)).where(
                ArticleLabel.annotator.like(f"{LLM_ANNOTATOR_PREFIX}%")
            )
        )
        or 0
    )
    llm_last_24h = int(
        db.scalar(
            select(func.count(ArticleLabel.id)).where(
                ArticleLabel.annotator.like(f"{LLM_ANNOTATOR_PREFIX}%"),
                ArticleLabel.reviewed_at >= day_ago,
            )
        )
        or 0
    )
    pending_backlog = int(
        db.scalar(select(func.count()).select_from(_unlabeled_query(None, 100_000).subquery()))
        or 0
    )

    human_label = aliased(ArticleLabel)
    llm_label = aliased(ArticleLabel)
    audited_rows = list(
        db.execute(
            select(human_label, llm_label).join(
                llm_label,
                and_(
                    llm_label.company_id == human_label.company_id,
                    llm_label.raw_article_id == human_label.raw_article_id,
                    llm_label.annotator.like(f"{LLM_ANNOTATOR_PREFIX}%"),
                ),
            ).where(
                human_label.annotator == INTERNAL_REVIEW_ACTOR,
                human_label.reviewed_at >= month_start,
            )
        ).all()
    )
    reviewed_count = len(audited_rows)
    agreement_count = sum(
        1
        for human, llm in audited_rows
        if (human.relevance_label, human.advertisement_label, human.sentiment_label)
        == (llm.relevance_label, llm.advertisement_label, llm.sentiment_label)
    )
    agreement_rate = (agreement_count / reviewed_count) if reviewed_count else None

    return {
        "enabled": bool(settings.llm_labeling_enabled and settings.openai_api_key),
        "model_name": settings.llm_labeling_model_name,
        "llm_labeled_total": llm_total,
        "llm_labeled_last_24h": llm_last_24h,
        "pending_backlog": pending_backlog,
        "audit": {
            "month": now.astimezone(SEOUL).strftime("%Y-%m"),
            "target_sample_size": settings.llm_labeling_audit_sample_size,
            "reviewed_count": reviewed_count,
            "agreement_count": agreement_count,
            "agreement_rate": agreement_rate,
        },
    }
