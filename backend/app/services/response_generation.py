"""Generate citation-bounded Korean response drafts for a detected risk event."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from typing import Any
from urllib.parse import urlsplit

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

SCHEMA_VERSION = 2
MAIN_RESPONSE = "main_response"
COMPETITOR_IMPACT = "competitor_impact"
ACTION_HORIZONS = ("immediate", "within_24h", "within_7d")

logger = logging.getLogger(__name__)
_draft_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="response-draft")


def _is_http_url(value: Any) -> bool:
    """Return whether a citation is an absolute HTTP(S) URL."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def _safe_allowed_urls(values) -> set[str]:
    """Keep exact input URLs that are safe to expose as citations."""
    return {
        value.strip()
        for value in values
        if isinstance(value, str) and _is_http_url(value)
    }


def _company_profile(company: Company | None) -> dict[str, Any] | None:
    """Serialize only the company attributes needed to scale a response."""
    if company is None:
        return None
    revenue = getattr(company, "annual_revenue_krw", None)
    revenue_100m = None
    if revenue is not None:
        revenue_100m = format(
            Decimal(int(revenue)) / Decimal(100_000_000),
            "f",
        )
    return {
        "id": getattr(company, "id", None),
        "name": getattr(company, "name", ""),
        "company_role": getattr(company, "company_role", "main"),
        "annual_revenue_krw": int(revenue) if revenue is not None else None,
        "annual_revenue_100m_krw": revenue_100m,
        "company_size_class": getattr(company, "company_size_class", None),
    }


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
            if not _is_http_url(item.url) or item.url in seen:
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


def _action(text: str, citations: list[str]) -> dict[str, Any]:
    """Build one citation-backed response action."""
    return {"action": text, "evidence_urls": citations[:3]}


def _recommended_actions(
    immediate: str,
    within_24h: str,
    within_7d: str,
    citations: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Build the three required response horizons for a scenario."""
    return {
        "immediate": [_action(immediate, citations)],
        "within_24h": [_action(within_24h, citations)],
        "within_7d": [_action(within_7d, citations)],
    }


def _uncertainty_text(precedents: list[dict]) -> str:
    if not precedents:
        return "현재 사건 근거는 연결되어 있지만 검증된 과거 유사 사례는 아직 없습니다."
    if any(item.get("verification_status") != "verified" for item in precedents):
        return "사람이 아직 검증하지 않은 검색 결과는 사례 근거가 아닌 후보로만 표시했습니다."
    return "모든 사례는 검증된 출처에 연결되어 있습니다."


def _template_content(
    event: RiskEvent,
    risk_types: list[str],
    evidence: list[dict],
    precedents: list[dict],
    source_company: Company | None = None,
    target_main_company: Company | None = None,
    generation_kind: str = MAIN_RESPONSE,
) -> dict:
    """Create two deterministic, grounded scenarios when the LLM is unavailable."""
    type_names = [TYPE_LABELS.get(item, item) for item in risk_types]
    citations = list(
        _safe_allowed_urls(
            item.get("url")
            for item in [*evidence, *precedents]
            if isinstance(item, dict)
        )
    )
    citations.sort()
    common = {
        "risk_summary": event.summary or f"{', '.join(type_names)} 위험 신호가 감지되었습니다.",
        "risk_types": risk_types,
        "evidence": [
            {
                "title": item["title"],
                "url": item["url"],
                "reason": "현재 위험 구간에 포함된 근거 기사",
            }
            for item in evidence
            if isinstance(item, dict) and item.get("url") in citations
        ],
        "precedents": [
            item
            for item in precedents
            if isinstance(item, dict) and item.get("url") in citations
        ],
        "uncertainty": _uncertainty_text(precedents),
    }

    if generation_kind == COMPETITOR_IMPACT:
        source_name = getattr(source_company, "name", "경쟁사")
        main_name = getattr(target_main_company, "name", "메인 기업")
        common["scenarios"] = [
            {
                "title": "경쟁사 위험의 직접 전이",
                "assumption": f"{source_name}과 {main_name}의 고객군·공급망 또는 판매 채널이 일부 겹친다.",
                "possible_impact": f"{main_name}에도 같은 문제에 관한 고객 문의와 거래처 점검 요구가 증가할 수 있다.",
                "transmission_path": f"{source_name} 사건 보도 → 업종 전체 불안 확대 → 메인 기업 제품·운영에 대한 검증 요구 증가",
                "early_indicators": [
                    "메인 기업 관련 문의·민원 증가",
                    "동일 위험 키워드의 기사량 증가",
                    "공급사 또는 판매 채널의 추가 확인 요청",
                ],
                "recommended_actions": _recommended_actions(
                    "근거 기사와 메인 기업의 동일 노출 지점을 즉시 대조한다.",
                    "고객·공급망·채널별 잠재 영향 범위와 선제 안내 기준을 정리한다.",
                    "노출 지점의 보완 조치와 조기 지표를 주간 단위로 재점검한다.",
                    citations,
                ),
            },
            {
                "title": "업종 전반의 규제·평판 확산",
                "assumption": f"{source_name}의 사건이 개별 기업 문제가 아니라 업종 관행 문제로 해석된다.",
                "possible_impact": f"{main_name}에 규제기관·언론·소비자의 비교 검증이 집중되거나 반사이익과 동반 평판 위험이 함께 발생할 수 있다.",
                "transmission_path": "개별 사건 → 업종 관행 보도·규제 검토 → 메인 기업 비교 노출과 시장 기대 변화",
                "early_indicators": [
                    "업종 단위 규제·정책 언급 증가",
                    f"{source_name} 비교 기사와 검색량 증가",
                    "메인 기업 신뢰도·매출 전환 지표의 급격한 변화",
                ],
                "recommended_actions": _recommended_actions(
                    "업종 공통 쟁점과 메인 기업의 차별화된 통제 근거를 확인한다.",
                    "규제·홍보·영업 부서가 사용할 사실 기반 비교 자료와 문답을 준비한다.",
                    "시장 반응과 규제 동향을 반영해 통제 절차와 대외 메시지를 보완한다.",
                    citations,
                ),
            },
        ]
    else:
        common["scenarios"] = [
            {
                "title": "사실 확인과 피해 확산 차단",
                "rationale": "초기 정보가 불완전한 동안 확인된 사실과 노출 범위를 먼저 고정해 추가 피해와 오판을 줄인다.",
                "recommended_actions": _recommended_actions(
                    "근거 기사와 원문을 대조하고 법무·홍보·사업 담당자로 사실 확인 채널을 연다.",
                    "확인된 사실, 영향 범위와 고객 문의 대응 문안을 정리한다.",
                    "사건 원인과 재발 방지 조치의 이행 상태를 추적한다.",
                    citations,
                ),
            },
            {
                "title": "이해관계자 소통과 운영 복원",
                "rationale": "사건의 직접 대응과 별개로 고객·거래처의 불확실성을 낮추고 핵심 운영의 연속성을 확보한다.",
                "recommended_actions": _recommended_actions(
                    "추가 기사와 고객·거래처 반응을 모니터링하고 핵심 운영 중단 가능성을 점검한다.",
                    "상황별 안내 문안, 대체 운영 절차와 의사결정 책임자를 확정한다.",
                    "운영 복원 결과와 통제 개선 내용을 검증해 이해관계자에게 투명하게 공유한다.",
                    citations,
                ),
            },
        ]
    return common


def _action_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "evidence_urls"],
        "properties": {
            "action": {"type": "string"},
            "evidence_urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
    }


def _recommended_actions_schema() -> dict[str, Any]:
    action_item = _action_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(ACTION_HORIZONS),
        "properties": {
            horizon: {
                "type": "array",
                "items": action_item,
                "minItems": 1,
            }
            for horizon in ACTION_HORIZONS
        },
    }


def _scenario_schema(generation_kind: str) -> dict[str, Any]:
    if generation_kind == COMPETITOR_IMPACT:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title",
                "assumption",
                "possible_impact",
                "transmission_path",
                "early_indicators",
                "recommended_actions",
            ],
            "properties": {
                "title": {"type": "string"},
                "assumption": {"type": "string"},
                "possible_impact": {"type": "string"},
                "transmission_path": {"type": "string"},
                "early_indicators": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "recommended_actions": _recommended_actions_schema(),
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "rationale", "recommended_actions"],
        "properties": {
            "title": {"type": "string"},
            "rationale": {"type": "string"},
            "recommended_actions": _recommended_actions_schema(),
        },
    }


def _response_provider_ready(settings) -> bool:
    if settings.response_generation_provider == "ollama":
        return bool(settings.ollama_base_url)
    return bool(settings.openai_api_key)


def _call_openai_content(prompt: dict, schema: dict, model_name: str, api_key: str) -> dict:
    from openai import OpenAI

    response = OpenAI(api_key=api_key).responses.create(
        model=model_name,
        input=json.dumps(prompt, ensure_ascii=False, default=str),
        text={
            "format": {
                "type": "json_schema",
                "name": "risk_response_draft_v2",
                "strict": True,
                "schema": schema,
            }
        },
    )
    return json.loads(response.output_text)


def _call_ollama_content(prompt: dict, schema: dict, model_name: str, base_url: str) -> dict:
    import httpx

    with httpx.Client(timeout=180.0) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model_name,
                "messages": [
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}
                ],
                "stream": False,
                "format": schema,
            },
        )
        response.raise_for_status()
    return json.loads(response.json()["message"]["content"])


def _llm_content(
    event: RiskEvent,
    risk_types: list[str],
    evidence: list[dict],
    precedents: list[dict],
    source_company: Company | None = None,
    target_main_company: Company | None = None,
    generation_kind: str = MAIN_RESPONSE,
) -> dict | None:
    """Ask the configured model for 2-5 grounded scenarios of the required kind."""
    settings = get_settings()
    if not _response_provider_ready(settings):
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
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "risk_summary",
            "risk_types",
            "evidence",
            "precedents",
            "scenarios",
            "uncertainty",
        ],
        "properties": {
            "risk_summary": {"type": "string"},
            "risk_types": {"type": "array", "items": {"type": "string"}},
            "evidence": {
                "type": "array",
                "items": citation_item,
                "minItems": 1,
            },
            "precedents": {"type": "array", "items": precedent_item},
            "scenarios": {
                "type": "array",
                "items": _scenario_schema(generation_kind),
                "minItems": 2,
                "maxItems": 5,
            },
            "uncertainty": {"type": "string"},
        },
    }
    instruction = (
        "한국어 기업 위험 대응 초안을 작성하라. 2개에서 5개의 서로 다른 대응 경우를 제시하고, "
        "제공된 URL만 인용하며 모든 행동에 하나 이상의 근거 URL을 연결하라. 후보 사례는 검증된 "
        "사실처럼 단정하지 말고 외부 전송이나 자동 실행을 지시하지 마라."
    )
    if generation_kind == COMPETITOR_IMPACT:
        instruction += (
            " 위험 발생 기업의 직접 대응안을 쓰는 것이 아니라, 이 사건이 메인 기업에 미칠 수 있는 "
            "서로 다른 영향·전파 경로·조기 지표와 영향별 대응을 작성하라."
        )
    else:
        instruction += " 메인 기업 자체의 위험에 대한 서로 다른 직접 대응 방안을 작성하라."

    prompt = {
        "instruction": instruction,
        "generation_kind": generation_kind,
        "source_company": _company_profile(source_company),
        "target_main_company": _company_profile(target_main_company),
        "event": {
            "summary": event.summary,
            "risk_probability": event.risk_probability,
            "risk_types": risk_types,
        },
        "evidence": evidence,
        "precedents": precedents,
    }
    try:
        if settings.response_generation_provider == "ollama":
            return _call_ollama_content(
                prompt, schema, settings.response_model_name, settings.ollama_base_url
            )
        return _call_openai_content(
            prompt, schema, settings.response_model_name, settings.openai_api_key
        )
    except Exception:
        logger.exception("LLM response draft generation call failed for event_id=%s", event.id)
        return None


def _filter_citations(content: dict, allowed_urls: set[str]) -> dict:
    """Recursively remove citations that were not safe, HTTP(S) input URLs."""
    safe_allowed = _safe_allowed_urls(allowed_urls)

    def scrub(value):
        if isinstance(value, list):
            cleaned_items = []
            for item in value:
                cleaned = scrub(item)
                if cleaned is not None:
                    cleaned_items.append(cleaned)
            return cleaned_items
        if not isinstance(value, dict):
            return value

        if "url" in value:
            candidate = value.get("url")
            if not isinstance(candidate, str):
                return None
            candidate = candidate.strip()
            if candidate not in safe_allowed or not _is_http_url(candidate):
                return None

        cleaned_dict: dict[str, Any] = {}
        for key, item in value.items():
            if key == "url":
                cleaned_dict[key] = item.strip()
            elif key == "evidence_urls":
                source_urls = item if isinstance(item, list) else []
                cleaned_dict[key] = [
                    candidate.strip()
                    for candidate in source_urls
                    if (
                        isinstance(candidate, str)
                        and candidate.strip() in safe_allowed
                        and _is_http_url(candidate.strip())
                    )
                ]
            else:
                cleaned = scrub(item)
                if cleaned is not None:
                    cleaned_dict[key] = cleaned

        if (
            "action" in value
            and "evidence_urls" in value
            and not cleaned_dict.get("evidence_urls")
        ):
            return None
        return cleaned_dict

    cleaned = scrub(content)
    if not isinstance(cleaned, dict):
        return {}
    cleaned.setdefault("evidence", [])
    cleaned.setdefault("precedents", [])
    cleaned.setdefault("scenarios", [])
    return cleaned


def _matches_v2_contract(content: dict, generation_kind: str) -> bool:
    """Validate the minimum shape needed by clients after citation filtering."""
    if not isinstance(content, dict):
        return False
    required_common = {
        "risk_summary",
        "risk_types",
        "evidence",
        "precedents",
        "scenarios",
        "uncertainty",
    }
    if not required_common.issubset(content):
        return False
    if not isinstance(content["evidence"], list) or not content["evidence"]:
        return False
    if not isinstance(content["precedents"], list):
        return False
    scenarios = content.get("scenarios")
    if not isinstance(scenarios, list) or not 2 <= len(scenarios) <= 5:
        return False
    scenario_fields = (
        {
            "title",
            "assumption",
            "possible_impact",
            "transmission_path",
            "early_indicators",
            "recommended_actions",
        }
        if generation_kind == COMPETITOR_IMPACT
        else {"title", "rationale", "recommended_actions"}
    )
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not scenario_fields.issubset(scenario):
            return False
        if generation_kind == COMPETITOR_IMPACT and not isinstance(
            scenario.get("early_indicators"), list
        ):
            return False
        actions = scenario.get("recommended_actions")
        if not isinstance(actions, dict):
            return False
        for horizon in ACTION_HORIZONS:
            items = actions.get(horizon)
            if not isinstance(items, list) or not items:
                return False
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("action"), str)
                    or not item.get("evidence_urls")
                ):
                    return False
    return True


def _generation_context(db, source_company: Company) -> tuple[Company, str]:
    """Resolve whether the event needs a direct or competitor-impact draft."""
    role = getattr(source_company, "company_role", "main")
    if role != "competitor":
        return source_company, MAIN_RESPONSE

    user_id = getattr(source_company, "user_id", None)
    if user_id is None:
        raise ValueError("경쟁사의 소유 사용자를 찾을 수 없습니다.")
    target_main_company = db.scalar(
        select(Company)
        .where(
            Company.user_id == user_id,
            Company.company_role == "main",
        )
        .order_by(Company.id)
        .limit(1)
    )
    if target_main_company is None:
        raise ValueError("사용자의 메인 기업을 찾을 수 없습니다.")
    return target_main_company, COMPETITOR_IMPACT


def generate_response_draft(risk_event_id: int, force: bool = False) -> ResponseDraft:
    """Retrieve grounded evidence and persist a v2 draft that always requires approval."""
    settings = get_settings()
    with SessionLocal() as db:
        event = db.get(RiskEvent, risk_event_id)
        if event is None:
            raise ValueError("위험 이벤트를 찾을 수 없습니다.")
        reviewed_label = authoritative_risk_label(db, risk_event_id)
        if reviewed_label is not None and not reviewed_label.is_risk:
            raise ValueError("관리에서 정상 사건으로 확정되어 대응 초안을 생성하지 않습니다.")

        source_company = db.get(Company, event.company_id)
        if source_company is None:
            raise ValueError("기업을 찾을 수 없습니다.")
        target_main_company, generation_kind = _generation_context(db, source_company)
        user_id = getattr(source_company, "user_id", None)
        if user_id is None:
            raise ValueError("기업의 소유 사용자를 찾을 수 없습니다.")

        if not force:
            existing = db.scalar(
                select(ResponseDraft)
                .where(
                    ResponseDraft.risk_event_id == risk_event_id,
                    ResponseDraft.schema_version == SCHEMA_VERSION,
                    ResponseDraft.user_id == user_id,
                    ResponseDraft.source_company_id == source_company.id,
                    ResponseDraft.target_main_company_id == target_main_company.id,
                    ResponseDraft.generation_kind == generation_kind,
                )
                .order_by(ResponseDraft.created_at.desc())
                .limit(1)
            )
            if existing is not None and (
                reviewed_label is None
                or existing.created_at >= reviewed_label.reviewed_at
            ):
                return existing

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
            article_rows.sort(
                key=lambda article: evidence_order.get(article.id, len(evidence_order))
            )
        evidence = [
            {
                "title": article.title,
                "url": article.url.strip(),
                "summary": article.summary or "",
                "publisher": article.source,
                "published_at": article.published_at,
                "verification_status": "current_evidence",
            }
            for article in article_rows
            if _is_http_url(article.url)
        ]
        if not evidence:
            raise ValueError("HTTP(S) URL이 연결된 근거 기사가 없어 대응 초안을 생성할 수 없습니다.")

        risk_types = (
            list(dict.fromkeys(reviewed_label.risk_types or []))
            if reviewed_label is not None
            else list(
                db.scalars(
                    select(RiskEventType.risk_type)
                    .where(RiskEventType.risk_event_id == risk_event_id)
                    .order_by(
                        RiskEventType.is_primary.desc(),
                        RiskEventType.probability.desc(),
                    )
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
        matching_cases = [
            case
            for case in cases
            if set(case.risk_types or []) & set(risk_types)
        ][:5]
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
                if not _is_http_url(source.url):
                    continue
                precedents.append(
                    {
                        "title": case.title,
                        "summary": case.summary,
                        "url": source.url.strip(),
                        "verification_status": "verified",
                    }
                )
        if not precedents:
            live = _live_case_candidates(source_company, risk_types[0])
            precedents.extend(live)
            for item in live:
                existing_source = db.scalar(
                    select(CaseSource).where(CaseSource.url == item["url"]).limit(1)
                )
                if existing_source is not None:
                    continue
                case = CaseRecord(
                    title=item["title"],
                    organization=source_company.name,
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

        allowed_urls = _safe_allowed_urls(
            item["url"] for item in [*evidence, *precedents]
        )
        content = _llm_content(
            event,
            risk_types,
            evidence,
            precedents,
            source_company,
            target_main_company,
            generation_kind,
        )
        model_name = settings.response_model_name
        if content is not None:
            content = _filter_citations(content, allowed_urls)
            content["risk_types"] = risk_types
        if content is None or not _matches_v2_contract(content, generation_kind):
            content = _filter_citations(
                _template_content(
                    event,
                    risk_types,
                    evidence,
                    precedents,
                    source_company,
                    target_main_company,
                    generation_kind,
                ),
                allowed_urls,
            )
            model_name = "grounded-template-v2"

        draft = ResponseDraft(
            risk_event_id=risk_event_id,
            user_id=user_id,
            source_company_id=source_company.id,
            target_main_company_id=target_main_company.id,
            generation_kind=generation_kind,
            schema_version=SCHEMA_VERSION,
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
            logger.exception(
                "response draft generation failed for risk event %s",
                risk_event_id,
            )

    _draft_executor.submit(_run)
