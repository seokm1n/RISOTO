"""3단계 근거 수집 (문서 §5). 보고서를 쓰기 **전에** 근거를 모은다.

순서가 이 방향이어야 하는 이유(문서 §5): 생성 후 근거를 첨부하면 LLM이 근거 없이 쓰고,
나중에 붙인 자료와 본문이 어긋난다. 인용한 사례가 실제로 존재하는지 검증할 대상도 없어진다.

네 종류를 모으고, 각각 찾는 방식이 다르다:
  - 원문        : 부정 강도 상위 + 확산 규모 상위 혼합 선별
  - 과거 사례   : 유형으로 좁힌 뒤 유사도 검색      (현재 비어 있음 -> no_case_mode)
  - 정량 근거   : 판정 근거 지표를 현재값·기준값과 함께 (상단 attribution 그대로)
  - 적용 법령   : 유형 -> 조항 결정적 매핑 조회      (현재 비어 있음 -> no_regulation_mode)

**혼합 선별인 이유**: 부정 강도만으로 뽑으면 극단적인 소수 의견만 올라오고, 확산 규모만으로
뽑으면 자극적인 것만 들어온다. 둘을 절반씩 섞어야 '많이 퍼진 불만'과 '강한 불만'이 같이 보인다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import keywords
from .retrieval import (
    CaseRetriever,
    NullCaseRetriever,
    NullRegulationMapper,
    PastCase,
    Regulation,
    RegulationMapper,
)
from .schema import AlertPayload, Attribution, Mention

MAX_MENTIONS = 10  # 문서 §2: 원문은 최대 10건


@dataclass
class Evidence:
    mentions: list[Mention] = field(default_factory=list)
    cases: list[PastCase] = field(default_factory=list)
    attribution: list[Attribution] = field(default_factory=list)
    regulations: list[Regulation] = field(default_factory=list)

    # 자산이 비었을 때 켜지는 플래그. generate.py가 인용을 금지하고,
    # verify.py 규칙 1이 실제로 인용 0건인지 검사한다.
    no_case_mode: bool = True
    no_regulation_mode: bool = True

    @property
    def allowed_mention_ids(self) -> set[str]:
        """자동 검증 2번(인용 원문 대조)의 정답 집합."""
        return {m.mention_id for m in self.mentions}

    @property
    def allowed_case_ids(self) -> set[str]:
        return {c.case_id for c in self.cases}


def _engagement(m: Mention) -> int:
    """확산 규모 대리 지표. like/reply는 유튜브에만 있어 없는 소스는 0으로 떨어진다 -
    그래서 이 값만으로 정렬하지 않고 아래에서 부정 강도와 절반씩 섞는다."""
    return (m.like_count or 0) + (m.reply_count or 0) * 2


def _is_negative(m: Mention) -> bool:
    if m.sentiment is not None:
        return str(m.sentiment).lower() in ("negative", "neg", "부정")
    return keywords.looks_negative(m.text)


def select_mentions(payload: AlertPayload, limit: int = MAX_MENTIONS) -> list[Mention]:
    """부정 강도 상위 절반 + 확산 규모 상위 절반. 중복은 제거한다."""
    mentions = list(payload.mentions)
    if not mentions:
        return []

    half = max(1, limit // 2)
    negatives = [m for m in mentions if _is_negative(m)]

    # 상단에서 sentiment가 왔으면 그 안에서 참여도 높은 순, 아니면 그냥 참여도 순으로
    # 부정 쪽을 추린다(부정 '강도' 점수가 따로 안 오므로 참여도를 2차 기준으로 쓴다).
    by_negative = sorted(negatives, key=_engagement, reverse=True)[:half]
    by_spread = sorted(mentions, key=_engagement, reverse=True)[:half]

    picked: list[Mention] = []
    seen: set[str] = set()
    for m, reason in [(m, "부정_상위") for m in by_negative] + [(m, "확산_상위") for m in by_spread]:
        if m.mention_id in seen:
            continue
        seen.add(m.mention_id)
        if m.pick_reason is None:
            m.pick_reason = reason
        picked.append(m)
        if len(picked) >= limit:
            break

    # 부정/확산 어느 쪽에도 안 걸렸는데 자리가 남으면 나머지로 채운다.
    if len(picked) < limit:
        for m in mentions:
            if m.mention_id not in seen:
                m.pick_reason = m.pick_reason or "보충"
                picked.append(m)
                seen.add(m.mention_id)
                if len(picked) >= limit:
                    break
    return picked


def build(
    payload: AlertPayload,
    risk_type_code: str,
    case_retriever: CaseRetriever | None = None,
    regulation_mapper: RegulationMapper | None = None,
) -> Evidence:
    """검색기를 주입하지 않으면 Null 구현이 쓰이고 no_*_mode가 켜진다(retrieval.py 참고)."""
    case_retriever = case_retriever or NullCaseRetriever()
    regulation_mapper = regulation_mapper or NullRegulationMapper()

    mentions = select_mentions(payload)
    query_text = " ".join(m.text for m in mentions[:3])[:500]
    cases = case_retriever.search(risk_type_code, query_text, top_k=3)
    regulations = regulation_mapper.lookup(risk_type_code)

    return Evidence(
        mentions=mentions,
        cases=cases,
        attribution=list(payload.attribution),
        regulations=regulations,
        no_case_mode=not cases,
        no_regulation_mode=not regulations,
    )
