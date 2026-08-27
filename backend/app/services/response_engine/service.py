"""RiskEvent -> 대응 시나리오 생성 -> ResponseDraft 저장.

기존 response_generation.generate_response_draft를 대체하는 진입점이다. 라우터
(`routers/governance.py`)는 이 모듈의 generate_response_draft를 호출하면 되고,
반환 타입(ResponseDraft)과 저장 형식은 기존과 같게 맞췄다.

**기존 구현과 달라지는 것**
  - 탐지 유형(8개) -> 대응 세부 유형(13개)으로 한 단계 좁힌 뒤 원칙을 고른다
  - 유형별 대응 원칙 + 담당 주체별 지침 + 상황별 RAG 보충을 프롬프트에 넣는다
  - 관점이 다른 시나리오를 2~3개 만들어 담당자가 고르게 한다
  - 자동 검증 10규칙을 돌리고, 실패하면 위반 항목을 지정해 1회 재생성한다
  - 국내 법령 매핑에서 시행 중인 조문만 의무로 넣는다

content 구조는 기존 v2 계약과 다르므로 SCHEMA_VERSION을 3으로 올린다. 프런트가 v2를
기대하고 있다면 이 값으로 분기할 수 있다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Company, ResponseDraft, RiskEvent, RiskEventArticle, NewsArticle

from . import classify, evidence, generate, tier, verify
from ._llm import response_model
from .case_search import TeamCaseRetriever
from .principles import PROMPT_VERSION
from .retrieval import KoreanRegulationMapper
from .risk_types import get as get_type
from .schema import AlertPayload

SCHEMA_VERSION = 3
MAIN_RESPONSE = "main_response"
COMPETITOR_IMPACT = "competitor_impact"

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="response-engine")


def _detection_scores(event: RiskEvent) -> dict[str, float] | str:
    """RiskEvent에서 탐지 유형 점수를 뽑는다.

    멀티라벨 점수가 남아 있으면 그대로 쓴다 - classify.refine이 1·2위 격차를 보고
    애매하면 두 상위의 세부 유형을 모두 후보에 넣는다. 점수가 없고 primary_type만
    있으면 문자열로 넘기되, 그때는 상단 확신도를 알 수 없다는 표시가 결과에 남는다.
    """
    # 현재 RiskEvent 스키마에는 유형별 점수가 남지 않고 primary_type만 있다. 점수를
    # 저장하게 되면(risk_type_scores 등) 여기만 고치면 refine이 애매 구간을 처리한다.
    scores = getattr(event, "risk_type_scores", None)
    if isinstance(scores, dict) and scores:
        return {k: float(v) for k, v in scores.items() if isinstance(v, (int, float))}
    return event.primary_type or "reputation_consumer"


def _payload_from_event(db, event: RiskEvent, company: Company) -> AlertPayload:
    """DB의 RiskEvent를 대응 엔진 입력으로 옮긴다."""
    articles = db.scalars(
        select(NewsArticle)
        .join(RiskEventArticle, RiskEventArticle.article_id == NewsArticle.id)
        .where(RiskEventArticle.risk_event_id == event.id)
        .limit(10)
    ).all()

    mentions = [
        {
            "mention_id": str(a.id),
            "source": getattr(a, "source", None),
            "text": (a.summary or a.title or "")[:600],
            "url": a.url,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "sentiment": a.sentiment_label,
        }
        for a in articles
    ]

    return AlertPayload.from_dict({
        "alert_id": f"RE-{event.id}",
        "company_id": str(company.id),
        "company_name": company.name,
        "industry": _industry_name(db, company),
        "crisis_probability": event.risk_probability,
        "model_version": "risk-detector",
        "escalation_tier": event.severity,
        "mentions": mentions,
        "company_role": getattr(company, "company_role", "main"),
    })


def _industry_name(db, company: Company) -> str | None:
    """Company.industry_id -> 업종명. 조회 실패는 치명적이지 않으므로 None으로 넘긴다."""
    if not getattr(company, "industry_id", None):
        return None
    try:
        from app.models import Industry

        row = db.get(Industry, company.industry_id)
        return getattr(row, "name", None) if row else None
    except Exception:
        return None


def _resolve_context(db, source_company: Company) -> tuple[Company, str]:
    """메인/동종 경로를 가른다. 기존 response_generation._generation_context와 같은 규칙."""
    if getattr(source_company, "company_role", "main") != "competitor":
        return source_company, MAIN_RESPONSE
    target = db.scalar(
        select(Company)
        .where(Company.user_id == source_company.user_id, Company.company_role == "main")
        .order_by(Company.id)
        .limit(1)
    )
    if target is None:
        raise ValueError("사용자의 메인 기업을 찾을 수 없습니다.")
    return target, COMPETITOR_IMPACT


def generate_response_draft(risk_event_id: int, force: bool = False) -> ResponseDraft:
    """위기 이벤트 하나에 대해 대응 시나리오를 만들어 ResponseDraft로 저장한다."""
    settings = get_settings()
    with SessionLocal() as db:
        event = db.get(RiskEvent, risk_event_id)
        if event is None:
            raise ValueError(f"risk event {risk_event_id}을(를) 찾을 수 없습니다.")
        source_company = db.get(Company, event.company_id)
        if source_company is None:
            raise ValueError("위기 이벤트의 기업을 찾을 수 없습니다.")

        if not force:
            existing = db.scalar(
                select(ResponseDraft)
                .where(
                    ResponseDraft.risk_event_id == risk_event_id,
                    ResponseDraft.schema_version == SCHEMA_VERSION,
                )
                .order_by(ResponseDraft.id.desc())
                .limit(1)
            )
            if existing is not None:
                return existing

        target_company, generation_kind = _resolve_context(db, source_company)
        payload = _payload_from_event(db, event, source_company)
        if generation_kind == COMPETITOR_IMPACT:
            payload.main_company_name = target_company.name
            payload.main_company_industry = _industry_name(db, target_company)

        content, allowed_urls, model_name = _build_content(
            db, payload, event, generation_kind, target_company
        )

        draft = ResponseDraft(
            risk_event_id=risk_event_id,
            user_id=source_company.user_id,
            source_company_id=source_company.id,
            target_main_company_id=target_company.id,
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


def _build_content(db, payload, event, generation_kind, target_company):
    """분류 -> 등급 -> 근거 -> 시나리오 -> 검증. content dict와 허용 URL 집합을 돌려준다."""
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def _add(u):
        for k in usage:
            usage[k] += (u or {}).get(k, 0)

    # 1) 탐지 유형(8) -> 대응 세부 유형(13)
    cls = classify.refine(payload, _detection_scores(event))
    code = cls["risk_type"]
    rt = get_type(code)

    # 2) 대응 등급
    decision = tier.decide(code, payload)

    # 3) 근거 수집 - 검수 사례 우선, 모자란 만큼 검색. 법령은 시행 중인 것만 의무로.
    retriever = TeamCaseRetriever(company_name=payload.company_name, db=db)
    ev = evidence.build(
        payload, code,
        case_retriever=retriever,
        regulation_mapper=KoreanRegulationMapper(),
    )
    _add(retriever.last_usage)

    # 4) 관점이 다른 시나리오 생성
    from .rag import RagPrincipleProvider

    provider = RagPrincipleProvider()
    stances = (
        generate.DEFAULT_STANCES if decision.tier == "T3_긴급" else generate.DEFAULT_STANCES[:2]
    )
    drafts, gen_usage = generate.generate_scenarios(
        payload, code, ev, stances=stances, provider=provider
    )
    _add(gen_usage)

    # 5) 시나리오별 검증. 실패한 것만 위반 항목을 지정해 1회 재생성한다.
    verified = []
    for draft in drafts:
        vr = verify.verify(draft, ev, code)
        if vr.passed:
            verified.append((draft, vr))
            continue
        retried, retry_usage = generate.regenerate_with_feedback(
            payload, code, ev, draft, vr.violations, provider=provider
        )
        _add(retry_usage)
        retried.setdefault("scenario_stance", draft.get("scenario_stance", "?"))
        verified.append((retried, verify.verify(retried, ev, code)))

    passing = [(d, v) for d, v in verified if v.passed]
    status = "생성완료"
    if passing:
        candidates = passing
    else:
        candidates = verified
        status = "검증실패"

    kept_drafts, merge_notes = generate.dedupe_scenarios([d for d, _ in candidates])
    kept_ids = {id(d) for d in kept_drafts}
    kept = [(d, v) for d, v in candidates if id(d) in kept_ids]

    allowed_urls = {u for c in ev.cases for u in c.source_urls}
    allowed_urls |= {m.url for m in ev.mentions if m.url}

    content = {
        "engine": "response_engine",
        "status": status,
        "risk_type": code,
        "risk_type_label": rt.label,
        "detection_type": rt.parent,
        "stakeholder": rt.stakeholder.value,
        "classification": cls,
        "tier": decision.tier,
        "tier_policy": decision.policy,
        "tier_notes": decision.notes,
        "generation_kind": generation_kind,
        "selected_stance": kept[0][0].get("scenario_stance", "?") if kept else "",
        "scenario_notes": merge_notes,
        "scenarios": [
            {
                "stance": d.get("scenario_stance", "?"),
                "tradeoff": d.get("scenario_tradeoff", ""),
                "merged_stances": d.get("merged_stances", []),
                "report": d,
                "verification": {
                    "passed": v.passed,
                    "summary": v.summary(),
                    "rules": [asdict(r) for r in v.results],
                    "violations": v.violations,
                    "skipped": v.skipped,
                },
            }
            for d, v in kept
        ],
        "evidence": [
            {"mention_id": m.mention_id, "title": m.text[:120], "url": m.url,
             "source": m.source, "published_at": m.published_at}
            for m in ev.mentions
        ],
        "precedents": [
            {"case_id": c.case_id, "title": c.title, "summary": c.summary_what,
             "lesson": c.lesson, "url": (c.source_urls or [None])[0],
             "verification_status": "verified" if c.provenance == "curated" else "candidate"}
            for c in ev.cases
        ],
        "regulations": [
            {"law_name": r.law_name, "article": r.article, "requirement": r.requirement,
             "deadline_hours": r.deadline_hours, "is_upcoming": r.is_upcoming,
             "source_url": r.source_url}
            for r in ev.regulations
        ],
        "principle_version": PROMPT_VERSION,
        "usage": usage,
    }
    return content, allowed_urls, response_model()


def enqueue_response_draft(risk_event_id: int, force: bool = False) -> None:
    """수집 흐름을 막지 않고 백그라운드로 생성한다. 실패는 로그로만 남는다."""

    def _run() -> None:
        try:
            generate_response_draft(risk_event_id, force=force)
        except Exception:
            logger.exception("response draft 생성 실패 (risk_event=%s)", risk_event_id)

    _executor.submit(_run)
