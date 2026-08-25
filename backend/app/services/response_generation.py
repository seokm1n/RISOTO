"""Generate citation-bounded Korean response drafts for a detected risk event."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    CaseRecord,
    CaseSource,
    Company,
    NewsArticle,
    ResponseDraft,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
)
from app.services.news_collectors import NaverNewsCollector, TavilyNewsCollector
from app.services.risk_ground_truth import authoritative_risk_label


TYPE_LABELS = {
    "product_quality": "제품·품질",
    "safety_accident": "안전·사고",
    "security_privacy": "보안·개인정보",
    "legal_regulatory": "법률·규제",
    "labor_hr": "노동·인사",
    "financial_governance": "재무·지배구조",
    "supply_operations": "공급·운영",
    "reputation_consumer": "평판·소비자",
}

logger = logging.getLogger(__name__)
_draft_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="response-draft")


def _live_case_candidates(company: Company, risk_type: str) -> list[dict[str, Any]]:
    """Search domestic Korean evidence only when the verified case DB is insufficient."""
    settings = get_settings()
    query = f"{company.name} {TYPE_LABELS.get(risk_type, risk_type)} 유사 사례 대응"
    since = (datetime.now(timezone.utc) - timedelta(days=3650)).date()
    collectors = []
    if settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret:
        collectors.append(
            NaverNewsCollector(
                settings.naver_api_hub_client_id,
                settings.naver_api_hub_client_secret,
            )
        )
    if settings.tavily_api_key:
        collectors.append(TavilyNewsCollector(settings.tavily_api_key))
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for collector in collectors:
        try:
            items = collector.search(query, since)
        except Exception:
            continue
        for item in items[:5]:
            if item.url in seen:
                continue
            seen.add(item.url)
            results.append(
                {
                    "title": item.title,
                    "url": item.url,
                    "summary": item.summary or "",
                    "publisher": item.source,
                    "published_at": item.published_at,
                    "verification_status": "candidate",
                }
            )
    return results[:8]


def _template_content(
    event: RiskEvent,
    risk_types: list[str],
    evidence: list[dict],
    precedents: list[dict],
) -> dict:
    type_names = [TYPE_LABELS.get(item, item) for item in risk_types]
    citations = [item["url"] for item in evidence]
    return {
        "risk_summary": event.summary or f"{', '.join(type_names)} 위험 신호가 감지되었습니다.",
        "risk_types": risk_types,
        "evidence": [
            {
                "title": item["title"],
                "url": item["url"],
                "reason": "현재 위험 구간에 포함된 근거 기사",
            }
            for item in evidence
        ],
        "precedents": precedents,
        "recommended_actions": {
            "immediate": [
                {"action": "근거 기사와 원문 사실관계를 확인한다.", "evidence_urls": citations[:3]},
                {"action": "법무·홍보·사업 담당자에게 내부 상황 확인을 요청한다.", "evidence_urls": citations[:3]},
            ],
            "within_24h": [
                {"action": "확인된 사실, 영향 범위, 고객 문의 대응 문안을 정리한다.", "evidence_urls": citations[:3]}
            ],
            "within_7d": [
                {"action": "사건 원인과 재발 방지 조치의 이행 상태를 추적한다.", "evidence_urls": citations[:3]}
            ],
        },
        "uncertainty": (
            "현재 사건 근거는 연결되어 있지만 검증된 과거 유사 사례는 아직 없습니다."
            if not precedents
            else
            "사람이 아직 검증하지 않은 검색 결과는 사례 근거가 아닌 후보로만 표시했습니다."
            if any(item.get("verification_status") != "verified" for item in precedents)
            else "모든 사례는 검증된 출처에 연결되어 있습니다."
        ),
    }


def _llm_content(
    event: RiskEvent,
    risk_types: list[str],
    evidence: list[dict],
    precedents: list[dict],
) -> dict | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    citation_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "url", "reason"],
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "reason": {"type": "string"},
        },
    }
    precedent_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "summary", "url", "verification_status"],
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "url": {"type": "string"},
            "verification_status": {"type": "string"},
        },
    }
    action_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "evidence_urls"],
        "properties": {
            "action": {"type": "string"},
            "evidence_urls": {"type": "array", "items": {"type": "string"}},
        },
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["risk_summary", "risk_types", "evidence", "precedents", "recommended_actions", "uncertainty"],
        "properties": {
            "risk_summary": {"type": "string"},
            "risk_types": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": citation_item},
            "precedents": {"type": "array", "items": precedent_item},
            "recommended_actions": {
                "type": "object",
                "additionalProperties": False,
                "required": ["immediate", "within_24h", "within_7d"],
                "properties": {
                    "immediate": {"type": "array", "items": action_item},
                    "within_24h": {"type": "array", "items": action_item},
                    "within_7d": {"type": "array", "items": action_item},
                },
            },
            "uncertainty": {"type": "string"},
        },
    }
    prompt = {
        "instruction": (
            "한국어 기업 위험 대응 초안을 작성하라. 제공된 URL만 인용하고, 후보 사례는 검증된 사실처럼 단정하지 말며, "
            "즉시·24시간·7일 대응을 구분하라. 외부 전송이나 자동 실행을 지시하지 마라."
        ),
        "event": {
            "summary": event.summary,
            "risk_probability": event.risk_probability,
            "risk_types": risk_types,
        },
        "evidence": evidence,
        "precedents": precedents,
    }
    try:
        from openai import OpenAI

        response = OpenAI(api_key=settings.openai_api_key).responses.create(
            model=settings.response_model_name,
            input=json.dumps(prompt, ensure_ascii=False, default=str),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "risk_response_draft",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        return json.loads(response.output_text)
    except Exception:
        return None


def _filter_citations(content: dict, allowed_urls: set[str]) -> dict:
    """Remove any LLM-generated evidence or action citation not present in input."""
    content["evidence"] = [
        item for item in content.get("evidence", [])
        if isinstance(item, dict) and item.get("url") in allowed_urls
    ]
    content["precedents"] = [
        item for item in content.get("precedents", [])
        if isinstance(item, dict) and item.get("url") in allowed_urls
    ]
    actions = content.get("recommended_actions") or {}
    for horizon, items in list(actions.items()):
        if not isinstance(items, list):
            actions[horizon] = []
            continue
        for item in items:
            if isinstance(item, dict):
                item["evidence_urls"] = [url for url in item.get("evidence_urls", []) if url in allowed_urls]
        actions[horizon] = [
            item for item in items
            if isinstance(item, dict) and item.get("evidence_urls")
        ]
    content["recommended_actions"] = actions
    return content


def generate_response_draft(risk_event_id: int, force: bool = False) -> ResponseDraft:
    """Retrieve grounded evidence and persist a draft that always requires approval."""
    settings = get_settings()
    with SessionLocal() as db:
        event = db.get(RiskEvent, risk_event_id)
        if event is None:
            raise ValueError("위험 이벤트를 찾을 수 없습니다.")
        reviewed_label = authoritative_risk_label(db, risk_event_id)
        if reviewed_label is not None and not reviewed_label.is_risk:
            raise ValueError("관리에서 정상 사건으로 확정되어 대응 초안을 생성하지 않습니다.")
        if not force:
            existing = db.scalar(
                select(ResponseDraft)
                .where(ResponseDraft.risk_event_id == risk_event_id)
                .order_by(ResponseDraft.created_at.desc())
                .limit(1)
            )
            if existing is not None and (
                reviewed_label is None
                or existing.created_at >= reviewed_label.reviewed_at
            ):
                return existing
        company = db.get(Company, event.company_id)
        if company is None:
            raise ValueError("기업을 찾을 수 없습니다.")
        article_query = (
            select(NewsArticle)
            .join(RiskEventArticle, RiskEventArticle.article_id == NewsArticle.id)
            .where(RiskEventArticle.risk_event_id == risk_event_id)
            .order_by(RiskEventArticle.evidence_score.desc())
        )
        reviewed_evidence_ids = (
            list(dict.fromkeys(reviewed_label.evidence_article_ids or []))
            if reviewed_label is not None
            else []
        )
        if reviewed_label is not None:
            article_query = article_query.where(NewsArticle.id.in_(reviewed_evidence_ids))
        article_rows = db.execute(article_query).scalars().all()
        if reviewed_evidence_ids:
            evidence_order = {
                article_id: index for index, article_id in enumerate(reviewed_evidence_ids)
            }
            article_rows.sort(key=lambda article: evidence_order.get(article.id, len(evidence_order)))
        if not article_rows:
            raise ValueError("URL이 연결된 근거 기사가 없어 대응 초안을 생성할 수 없습니다.")
        evidence = [
            {
                "title": article.title,
                "url": article.url,
                "summary": article.summary or "",
                "publisher": article.source,
                "published_at": article.published_at,
                "verification_status": "current_evidence",
            }
            for article in article_rows
        ]
        risk_types = (
            list(dict.fromkeys(reviewed_label.risk_types or []))
            if reviewed_label is not None
            else list(
                db.scalars(
                    select(RiskEventType.risk_type)
                    .where(RiskEventType.risk_event_id == risk_event_id)
                    .order_by(RiskEventType.is_primary.desc(), RiskEventType.probability.desc())
                )
            )
        ) or [event.primary_type or "reputation_consumer"]
        cases = list(
            db.scalars(
                select(CaseRecord)
                .where(CaseRecord.verification_status == "verified")
                .order_by(CaseRecord.occurred_at.desc().nullslast())
                .limit(100)
            )
        )
        matching_cases = [case for case in cases if set(case.risk_types or []) & set(risk_types)][:5]
        precedents: list[dict] = []
        for case in matching_cases:
            sources = list(
                db.scalars(
                    select(CaseSource).where(
                        CaseSource.case_id == case.id,
                        CaseSource.verification_status == "verified",
                    )
                )
            )
            for source in sources:
                precedents.append(
                    {
                        "title": case.title,
                        "summary": case.summary,
                        "url": source.url,
                        "verification_status": "verified",
                    }
                )
        if not precedents:
            live = _live_case_candidates(company, risk_types[0])
            precedents.extend(live)
            for item in live:
                existing_source = db.scalar(
                    select(CaseSource).where(CaseSource.url == item["url"]).limit(1)
                )
                if existing_source is not None:
                    continue
                case = CaseRecord(
                    title=item["title"],
                    organization=company.name,
                    occurred_at=item["published_at"],
                    risk_types=risk_types,
                    summary=item["summary"] or "실시간 검색으로 발견된 검증 대기 사례",
                    actions={},
                    verification_status="candidate",
                )
                db.add(case)
                db.flush()
                db.add(
                    CaseSource(
                        case_id=case.id,
                        title=item["title"],
                        url=item["url"],
                        publisher=item["publisher"],
                        published_at=item["published_at"],
                        verification_status="candidate",
                    )
                )

        allowed_urls = {item["url"] for item in [*evidence, *precedents]}
        content = _llm_content(event, risk_types, evidence, precedents)
        if content is None:
            content = _template_content(event, risk_types, evidence, precedents)
            model_name = "grounded-template-v1"
        else:
            content = _filter_citations(content, allowed_urls)
            model_name = settings.response_model_name
        draft = ResponseDraft(
            risk_event_id=risk_event_id,
            model_name=model_name,
            content=content,
            evidence_urls=sorted(allowed_urls),
            approval_state="draft",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft


def enqueue_response_draft(risk_event_id: int, force: bool = False) -> None:
    """Generate without delaying collection; failures remain visible in logs and never stop ingestion."""
    def _run() -> None:
        try:
            generate_response_draft(risk_event_id, force=force)
        except Exception:
            logger.exception("response draft generation failed for risk event %s", risk_event_id)

    _draft_executor.submit(_run)
