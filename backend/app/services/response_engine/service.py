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
import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from sqlalchemy import select, text

from app.config import get_settings
from app.database import SessionLocal
from app.models import Company, ResponseDraft, RiskEvent, RiskEventArticle, NewsArticle
from app.services.risk_ground_truth import authoritative_risk_label

from . import classify, evidence, generate, impact, recommend, tier, verify
from ._llm import response_model
from .case_search import TeamCaseRetriever
from .principles import PROMPT_VERSION
from .retrieval import KoreanRegulationMapper
from .risk_types import get as get_type
from .schema import AlertPayload

SCHEMA_VERSION = 3
MAIN_RESPONSE = "main_response"
COMPETITOR_IMPACT = "competitor_impact"

# 동종 경로의 검증 실패 시 피드백 재생성 횟수. 메인 경로(_build_content의 시나리오별
# 1회 재생성)와 같은 정책이고, 최악 비용을 호출 2회로 묶어 둔다.
MAX_FEEDBACK_RETRIES = 1

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="response-engine")
_active_job_ids: set[int] = set()
_active_jobs_lock = threading.Lock()


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


MAX_EVIDENCE_ARTICLES = 10

# 수집기가 붙이는 강조 태그만 지운다. 실측상 <b>가 3,362건으로 거의 전부이고 나머지는
# 오검출이었다. `<[^>]*>` 같은 일반 제거를 쓰면 안 되는데, 국내 기사 본문이 꺾쇠를
# 제목 표기에 쓰기 때문이다 - <처음부터 끝까지 혼자서 보험>, <2026년 8월26일(수)>처럼
# 지워지면 안 되는 것이 함께 사라진다. HTML을 남기는 것보다 나쁜 결과가 된다.
_HTML_TAG = re.compile(r"</?(?:b|i|u|em|strong|span|p|br|div|a|img|font)\b[^>]*>", re.I)


def clean_text(text: str | None) -> str:
    """기사 요약에서 강조 태그와 HTML 엔티티를 걷어낸다.

    순서가 중요하다. 태그를 먼저 지우고 엔티티를 푼다 - 반대로 하면 `&lt;b&gt;`가 실제
    태그로 복원되어 다시 걸린다. 실측 엔티티는 &#39;(383건), &#34;(232건)가 대부분이다.
    """
    return re.sub(r"\s{2,}", " ", html.unescape(_HTML_TAG.sub("", text or ""))).strip()


def _payload_from_event(
    db,
    event: RiskEvent,
    company: Company,
    evidence_article_ids: list[int] | None = None,
) -> AlertPayload:
    """DB의 RiskEvent를 대응 엔진 입력으로 옮긴다.

    **정렬이 반드시 있어야 한다**: 한 이벤트에 기사가 수백 건 붙는다(실측 최대 661건).
    ORDER BY 없이 LIMIT만 걸면 PostgreSQL이 순서를 보장하지 않아, 같은 이벤트를 다시
    돌렸을 때 다른 기사 10건이 뽑히고 결론이 달라진다. 재현되지 않는 산출물은 비교도
    검증도 할 수 없다. 근거 점수가 높은 것부터 가져오고, 동점은 id로 갈라 확정한다.
    """
    article_query = (
        select(NewsArticle)
        .join(RiskEventArticle, RiskEventArticle.article_id == NewsArticle.id)
        .where(RiskEventArticle.risk_event_id == event.id)
        .order_by(
            RiskEventArticle.evidence_score.desc().nullslast(),
            NewsArticle.published_at.desc().nullslast(),
            NewsArticle.id.desc(),
        )
        .limit(MAX_EVIDENCE_ARTICLES)
    )
    if evidence_article_ids is not None:
        article_query = article_query.where(NewsArticle.id.in_(evidence_article_ids))
    articles = db.scalars(article_query).all()

    mentions = [
        {
            "mention_id": str(a.id),
            "source": getattr(a, "source", None),
            "text": clean_text(a.summary or a.title)[:600],
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
        # 어느 탐지 모델이 낸 경보인지 대응 산출에서 추적할 수 있어야 한다. 컬럼에 실제
        # 버전(lightgbm_auto_v3 등)이 들어 있는데 고정 문자열을 쓰고 있었다.
        "model_version": event.model_version or "risk-detector",
        "escalation_tier": event.severity,
        "mentions": mentions,
        "company_role": getattr(company, "company_role", "main"),
    })


def _no_evidence_content(event: RiskEvent, generation_kind: str, target_company: Company) -> dict:
    """근거 기사가 없을 때 저장할 내용. LLM을 부르지 않는다.

    프런트가 두 경로의 초안을 함께 그리므로 status와 generation_kind는 같은 자리에 둔다.
    사람이 무엇을 해야 하는지가 유일하게 중요한 정보라 needs_review와 사유만 남긴다.
    """
    return {
        "engine": "response_engine",
        "status": "근거부족_보류",
        "generation_kind": generation_kind,
        "main_company_name": target_company.name,
        "needs_review": True,
        "review_reason": (
            "이 위기 이벤트에 연결된 근거 기사가 없어 대응방안을 생성하지 않았습니다. "
            "탐지 경보만 있고 원문이 없는 상태이므로, 수집 파이프라인에서 기사 연결이 "
            "누락됐는지 먼저 확인해야 합니다."
        ),
        "detection": {
            "risk_probability": event.risk_probability,
            "severity": event.severity,
            "model_version": event.model_version,
        },
        "scenarios": [],
        "recommendation": None,
        "evidence": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }


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


def _same_detection(existing: ResponseDraft, event: RiskEvent) -> bool:
    """기존 초안이 지금과 같은 탐지 유형으로 만들어졌는가.

    content의 detection_type은 세부 유형(R코드)의 상위, 즉 상단 모델이 준 8개 유형이다.
    이 값이 event.primary_type과 같으면 다시 만들어도 같은 답이 나온다.

    근거부족_보류 초안에는 이 키가 없다. 그때는 같지 않다고 보아 재생성하게 두는 것이
    맞다 - 기사가 뒤늦게 붙었을 수 있고, 보류 상태를 계속 붙들고 있을 이유가 없다.
    """
    content = existing.content or {}
    recorded = content.get("detection_type")
    return bool(recorded) and recorded == event.primary_type


def generate_response_draft(risk_event_id: int, force: bool = False) -> ResponseDraft:
    """위기 이벤트 하나에 대해 대응 시나리오를 만들어 ResponseDraft로 저장한다."""
    with SessionLocal() as db:
        event = db.get(RiskEvent, risk_event_id)
        if event is None:
            raise ValueError(f"risk event {risk_event_id}을(를) 찾을 수 없습니다.")
        reviewed_label = authoritative_risk_label(db, risk_event_id)
        if reviewed_label is not None and not reviewed_label.is_risk:
            raise ValueError("관리에서 정상 사건으로 확정되어 대응 초안을 생성하지 않습니다.")
        source_company = db.get(Company, event.company_id)
        if source_company is None:
            raise ValueError("위기 이벤트의 기업을 찾을 수 없습니다.")
        target_company, generation_kind = _resolve_context(db, source_company)

        if not force:
            existing = db.scalar(
                select(ResponseDraft)
                .where(
                    ResponseDraft.risk_event_id == risk_event_id,
                    ResponseDraft.schema_version == SCHEMA_VERSION,
                    ResponseDraft.user_id == source_company.user_id,
                    ResponseDraft.source_company_id == source_company.id,
                    ResponseDraft.target_main_company_id == target_company.id,
                    ResponseDraft.generation_kind == generation_kind,
                )
                .order_by(ResponseDraft.id.desc())
                .limit(1)
            )
            # 이미 만든 초안이 있어도 탐지 유형이 바뀌었으면 다시 만들어야 한다. 유형이
            # 달라지면 대응 원칙·법령·시나리오가 통째로 달라지기 때문이다. 반대로 유형이
            # 같으면 같은 답이 나오므로 재생성은 비용만 쓴다.
            if existing is not None and (
                reviewed_label is None
                or existing.created_at >= reviewed_label.reviewed_at
            ) and _same_detection(existing, event) and (
                event.event_source != "story_v2"
                or event.last_response_revision >= event.evidence_revision
            ):
                if event.event_source == "story_v2":
                    event.response_generation_status = "generated"
                    event.response_generation_error = None
                    db.commit()
                return existing

        reviewed_evidence_ids = (
            list(dict.fromkeys(reviewed_label.evidence_article_ids or []))
            if reviewed_label is not None
            else None
        )
        payload = _payload_from_event(
            db,
            event,
            source_company,
            evidence_article_ids=reviewed_evidence_ids,
        )
        if generation_kind == COMPETITOR_IMPACT:
            payload.main_company_name = target_company.name
            payload.main_company_industry = _industry_name(db, target_company)

        if not payload.mentions:
            # 근거 기사가 하나도 없는 이벤트가 실재한다(공유 DB 실측 123건, 최고 확률
            # 0.94 critical 포함). 이대로 넘기면 원문 샘플이 "(원문 없음)"인 채로 모델이
            # 유형·방향·확신도를 확정한다. 근거 없는 판정을 정상 산출물로 저장하는 것이
            # 가장 나쁜 결과이므로, LLM을 부르지 않고 사람이 보게 남긴다.
            content = _no_evidence_content(event, generation_kind, target_company)
            allowed_urls, model_name = set(), response_model()
        elif generation_kind == COMPETITOR_IMPACT:
            content, allowed_urls, model_name = _build_peer_content(db, payload)
        else:
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
        if event.event_source == "story_v2":
            event.last_response_revision = event.evidence_revision
            event.response_generation_status = "generated"
            event.response_generation_error = None
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
    # 유형 세분화도 LLM을 부를 수 있다. 여기서 집계하지 않으면 메인 경로 사용량이
    # 호출 1회만큼 과소 보고되고, impact.analyze를 세는 동종 경로와 비교가 안 된다.
    _add(cls.pop("usage", None))
    code = cls["risk_type"]
    rt = get_type(code)

    # 2) 대응 등급
    decision = tier.decide(code, payload)

    # 3) 근거 수집 - 검수 사례 우선, 모자란 만큼 검색. 법령은 시행 중인 것만 의무로.
    retriever = TeamCaseRetriever(
        company_name=payload.company_name,
        db=db,
        exclude_urls=[m.url for m in payload.mentions if m.url],
    )
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

    kept_drafts, merge_notes, dedupe_usage = generate.dedupe_scenarios(
        [d for d, _ in candidates]
    )
    _add(dedupe_usage)
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


def _build_peer_content(db, payload):
    """동종 기업 경로: 영향 판단 -> (영향 있을 때만) 사례·법령 수집 -> 추천 생성 -> 검증.

    _build_content의 짝이지만 분류·등급·시나리오 단계가 없다 - 동종 기업 추천은
    '심플하게'가 요구사항이라 그 단계들이 성립하지 않는다(recommend.py docstring 참고).
    영향_없음이면 사례 검색·추천 생성을 아예 호출하지 않는 것이 이 경로의 비용 통제
    지점이다(impact.analyze의 proceed 게이트).
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def _add(u):
        for k in usage:
            usage[k] += (u or {}).get(k, 0)

    # 1) 영향 판단 - 유형(13개 중 하나)과 방향·경로를 LLM 한 호출에서 함께 확정한다.
    analysis = impact.analyze(payload)
    _add(analysis.pop("usage", None))
    rt = get_type(analysis["risk_type"])

    # 내부 peer dict는 recommend.py의 프롬프트·검증 함수가 읽는 키 이름 그대로 둔다
    # (독립 작업본의 PeerImpactResult 계약 유지 - 여기 이름을 바꾸면 build_system_prompt의
    # peer.get("company_name") 폴백이 조용히 엉뚱한 회사명을 만든다).
    peer = {
        "company_name": payload.company_name,            # 사안 발생 동종 기업
        "main_company_name": payload.main_company_name,  # 우리 기업
        "alert_id": payload.alert_id,
        "risk_type": analysis["risk_type"],
        "risk_type_label": rt.label,
        "impact_direction": analysis["impact_direction"],
        "impact_level": analysis["impact_level"],
        "impact_channels": analysis["impact_channels"],
        "reason": analysis["reason"],
        "watch_points": analysis["watch_points"],
        "confidence": analysis["confidence"],
        "needs_review": analysis["needs_review"],
        "missing_input_fields": payload.missing_fields(),
        "cases": [],
        "regulations": [],
    }
    impact_block = {
        k: peer[k]
        for k in (
            "impact_direction", "impact_level", "impact_channels",
            "reason", "watch_points", "confidence", "needs_review",
        )
    }
    base_content = {
        "engine": "response_engine",
        "content_kind": "peer_recommendation",
        "generation_kind": COMPETITOR_IMPACT,
        "peer_company_name": peer["company_name"],
        "main_company_name": peer["main_company_name"],
        "risk_type": peer["risk_type"],
        "risk_type_label": peer["risk_type_label"],
        "detection_type": rt.parent,
        "impact": impact_block,
        "recommender_version": recommend.RECOMMENDER_VERSION,
        "impact_version": impact.IMPACT_VERSION,
    }

    if not analysis["proceed"]:
        content = {
            **base_content,
            "status": "영향없음_종료",
            "recommendation": None,
            "cases": [],
            "regulations": [],
            "verification": None,
            "usage": usage,
        }
        return content, set(), response_model()

    # 2) 근거 수집 - 사례는 우리 기업 관점으로(검수 DB 우선 + 부족분 검색), 법령은
    #    verified 조문만. 채널 조건부 주입 여부는 recommend.build_user_prompt가 판단한다.
    retriever = TeamCaseRetriever(
        company_name=payload.main_company_name or "",
        db=db,
        exclude_urls=[m.url for m in payload.mentions if m.url],
    )
    query_text = " ".join(m.text for m in payload.mentions[:3])[:300]
    cases = retriever.search(analysis["risk_type"], query_text, top_k=3)
    _add(retriever.last_usage)
    # 동종 경로는 시행 중인 조문만 쓴다. 여기 블록은 recommend.build_user_prompt에서
    # "우리 쪽 점검 항목 후보"로 렌더링되는데, 시행 예정 조문(예: 산업안전보건법 제54조
    # 2027-01-08 시행)이 섞이면 아직 의무가 아닌 것을 지금 점검하라고 안내하게 된다.
    # 메인 경로는 [시행 예정] 블록으로 분리해 살려 두지만, 동종 추천은 한 줄짜리 참고
    # 목록이라 구분이 표현되지 않으므로 조회 시점에 거른다.
    regs = KoreanRegulationMapper(include_upcoming=False).lookup(analysis["risk_type"])

    peer["cases"] = [
        {
            "case_id": c.case_id, "title": c.title, "summary_what": c.summary_what,
            "lesson": c.lesson, "provenance": c.provenance,
        }
        for c in cases
    ]
    # 적용 요건(원사업자 해당 여부 등)은 회사마다 달라 사람이 확인해야 하는 값이라
    # 요건 문장에 붙여 내보낸다. 프롬프트 렌더러가 law_name/article/requirement 세 키만
    # 읽으므로, 여기서 합쳐야 단서가 보고서까지 살아 남는다.
    peer["regulations"] = [
        {
            "law_name": r.law_name,
            "article": r.article,
            "requirement": (
                r.requirement
                + (f" (※ 적용 요건 확인 필요: {r.applicability_note})" if r.applicability_note else "")
            ),
        }
        for r in regs
    ]

    # 3) 추천 생성 -> 검증 -> 실패 시 위반 항목을 지정해 1회 재생성 (메인 경로와 동일 정책).
    rec, gen_usage = recommend.recommend(peer)
    _add(gen_usage)
    violations = recommend.verify_recommendation(rec, peer)
    for _ in range(MAX_FEEDBACK_RETRIES):
        if not violations:
            break
        retried, retry_usage = recommend.regenerate_with_feedback(peer, rec, violations)
        _add(retry_usage)
        rec = retried
        violations = recommend.verify_recommendation(rec, peer)

    allowed_urls = {u for c in cases for u in (c.source_urls or [])}
    allowed_urls |= {m.url for m in payload.mentions if m.url}

    content = {
        **base_content,
        "status": "검증실패" if violations else "생성완료",
        "recommendation": rec,
        "cases": [
            {
                "case_id": c.case_id, "title": c.title, "summary_what": c.summary_what,
                "lesson": c.lesson, "provenance": c.provenance,
                "source_urls": list(c.source_urls or []),
            }
            for c in cases
        ],
        "regulations": peer["regulations"],
        "verification": {"passed": not violations, "violations": violations},
        "usage": usage,
    }
    return content, allowed_urls, response_model()


def enqueue_response_draft(risk_event_id: int, force: bool = False, auto: bool = False) -> None:
    """수집 흐름을 막지 않고 백그라운드로 생성한다. 실패는 로그로만 남는다.

    **이벤트 단위로 잠그는 이유**: 이 함수를 부르는 실시간 tick이 여러 곳에서 동시에 돈다
    (워커 2개짜리 스레드풀이고, 팀원들이 각자 백엔드를 띄운 채 같은 DB를 본다). 생성은
    LLM 호출 때문에 1분을 넘기므로, "기존 초안 있나" 검사와 저장 사이가 넓게 벌어진다.
    그 틈에 다른 워커가 같은 검사를 통과해 같은 이벤트로 초안을 하나 더 만든다.

    기다리지 않고(try) 잠근다. 이미 누가 만들고 있다면 그 결과가 곧 저장되므로 여기서
    줄 서 있을 이유가 없고, 워커를 1분 넘게 묶어 두면 다른 이벤트가 밀린다.
    """

    def _set_status(status: str, error: str | None = None) -> None:
        with SessionLocal() as status_db:
            event = status_db.get(RiskEvent, risk_event_id)
            if event is not None and event.event_source == "story_v2":
                event.response_generation_status = status
                event.response_generation_error = error
                status_db.commit()

    # 자동 생성만 스위치로 막는다. 담당자가 버튼으로 요청한 건은 의도가 명확하므로
    # 끄지 않는다. 생성 경로가 story_risk와 risk_analysis 두 갈래이고 서로 배타적이라
    # (한쪽을 끄면 다른 쪽이 켜진다) 각 호출부가 아니라 여기 한 곳에서 판단한다.
    if auto and not get_settings().response_draft_auto_enabled:
        _set_status("deferred")
        logger.info(
            "대응방안 자동 생성이 꺼져 있어 건너뜁니다 (risk_event=%s). "
            "RESPONSE_DRAFT_AUTO_ENABLED=true로 켜거나 수동 생성을 쓰세요.",
            risk_event_id,
        )
        return

    # 같은 프로세스에서는 DB 종류와 무관하게 중복 작업을 막는다. PostgreSQL의
    # advisory lock은 여러 API 프로세스 사이의 중복까지 추가로 막는다.
    with _active_jobs_lock:
        if risk_event_id in _active_job_ids:
            logger.info("response draft 생성이 이미 진행 중입니다 (risk_event=%s)", risk_event_id)
            return
        _active_job_ids.add(risk_event_id)

    def _run() -> None:
        try:
            lock_key = f"response-draft:{risk_event_id}"
            with SessionLocal() as db:
                if db.get_bind().dialect.name == "postgresql":
                    acquired = db.scalar(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                        {"key": lock_key},
                    )
                    if not acquired:
                        logger.info(
                            "response draft 생성 건너뜀 - 다른 워커가 처리 중 (risk_event=%s)",
                            risk_event_id,
                        )
                        return
                else:
                    acquired = False
                try:
                    _set_status("generating")
                    generate_response_draft(risk_event_id, force=force)
                except Exception as exc:
                    logger.exception("response draft 생성 실패 (risk_event=%s)", risk_event_id)
                    _set_status("failed", f"{type(exc).__name__}: 대응방안 자동 생성에 실패했습니다.")
                finally:
                    if acquired:
                        db.execute(
                            text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                            {"key": lock_key},
                        )
                        db.commit()
        finally:
            with _active_jobs_lock:
                _active_job_ids.discard(risk_event_id)

    try:
        _executor.submit(_run)
    except Exception:
        with _active_jobs_lock:
            _active_job_ids.discard(risk_event_id)
        raise


def recover_interrupted_response_drafts() -> int:
    """서버 중단으로 DB에 남은 생성 상태를 시작 시 다시 처리한다."""
    active_statuses = ("open", "monitoring", "acknowledged")
    with SessionLocal() as db:
        events = list(db.scalars(
            select(RiskEvent).where(
                RiskEvent.event_source == "story_v2",
                RiskEvent.status.in_(active_statuses),
                RiskEvent.response_generation_status.in_(("pending", "generating")),
            ).order_by(
                (RiskEvent.severity == "critical").desc(),
                RiskEvent.risk_probability.desc().nullslast(),
            )
        ))
        if not get_settings().response_draft_auto_enabled:
            for event in events:
                event.response_generation_status = "deferred"
                event.response_generation_error = None
            db.commit()
            return len(events)
        event_ids = [event.id for event in events]
        for event in events:
            event.response_generation_status = "pending"
            event.response_generation_error = None
        db.commit()
    for event_id in event_ids:
        enqueue_response_draft(event_id, auto=True)
    return len(event_ids)
