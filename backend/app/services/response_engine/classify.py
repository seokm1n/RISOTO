"""1단계 리스크 유형 분류: 키워드 사전 1차 -> 경합/미달 건만 LLM 2차.

문서 §3의 2단 구조를 그대로 구현한다. 명백한 건을 코드가 처리하므로 LLM 호출량이 줄고,
같은 입력에 같은 결과가 나온다(재현성).

**게이트 조건** (문서 §3):
  1위 점수 >= MIN_TOP_HITS  AND  1위/2위 >= TOP_RATIO  -> 키워드 확정
  둘 중 하나라도 미달 -> LLM 승격

**경계 규칙 R1 (T11 평판·루머)**: T11은 키워드만으로 확정하지 않고 항상 LLM으로 보낸다.
루머의 '내용'은 배송·품질 등 다른 유형의 어휘를 그대로 쓰기 때문에(예: "배송 다 밀렸다는
소문 돔") 키워드로는 T2와 T11이 갈리지 않는다. 실제로 갈리는 축은 어휘가 아니라 '사실로
확인됐는가'이고, 그건 문맥 판단이라 LLM 몫이다.
"""
from __future__ import annotations

from . import keywords, risk_types
from ._llm import response_model, structured_call
from .schema import AlertPayload

# 게이트 임계값. 문서 §11에 적힌 대로 라벨셋으로 튜닝해야 하는 잠정값이다.
MIN_TOP_HITS = 3
TOP_RATIO = 2.0

CLASSIFIER_VERSION = "classify-v0.1"

_SCHEMA = {
    "name": "risk_type_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "risk_type": {"type": "string", "enum": list(risk_types.CODES)},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["risk_type", "confidence", "reason"],
        "additionalProperties": False,
    },
}

_REFINE_PROMPT = """당신은 기업 리스크 모니터링 시스템의 세부 유형 판정기입니다.
상위 유형은 이미 정해져 있고, 아래 후보 중에서 이 사안에 맞는 것 하나를 고르는 것이 역할입니다.

[후보 - 이 중에서만 고르세요]
{catalog}

[판정 규칙]
- 대상 기업은 {company}입니다.
- 후보들은 상위 유형이 같지만 **대응 상대가 다릅니다.** 누구를 향해 말해야 하는 사안인지로
  가르세요. 예를 들어 같은 운영 차질이라도 소비자가 겪는 일인지, 입점 판매자가 겪는 일인지에
  따라 대응이 정반대가 됩니다.
- 사실로 확인된 사건인지, 아직 확인되지 않은 소문이 퍼지는 것 자체가 사안인지도 갈림길입니다.
  후자라면 평판·루머 쪽입니다.
- confidence는 0.0~1.0입니다. 원문이 짧거나 후보 간 판단이 갈리면 낮게 주세요.
- reason은 한국어 한 문장으로, 무엇을 근거로 골랐는지 적으세요."""

_SYSTEM_PROMPT = """당신은 기업 리스크 모니터링 시스템의 유형 분류기입니다.
아래 게시글들이 {company}에 대해 제기하는 리스크가 어느 유형에 해당하는지 하나만 고르세요.

[유형 목록]
{catalog}

[판정 규칙]
- 반드시 하나만 고릅니다. 여러 유형이 섞여 있으면 '이 사안의 핵심 쟁점이 무엇인가'로 정합니다.
  예: 배송이 늦은 것보다 그 뒤 상담 태도가 논란의 중심이면 T7입니다.
- T11(평판·루머)은 다른 유형과 어휘가 겹칩니다. 갈림길은 '내용의 소재'가 아니라
  **사실로 확인되었는지**입니다. 실제로 벌어진 사건이면 해당 사건의 유형(T1~T10)을 고르고,
  아직 확인되지 않은 소문·의혹·허위주장이 확산되는 것 자체가 사안이면 T11을 고르세요.
- confidence는 0.0~1.0입니다. 게시글이 짧거나 서로 다른 유형을 가리켜 판단이 갈리면
  낮게 주세요. 확신이 없을 때 억지로 높이지 마세요.
- reason은 한국어 한 문장으로, 무엇을 근거로 그 유형을 골랐는지 적으세요."""




def _negative_texts(payload: AlertPayload) -> list[str]:
    """부정 문맥의 원문만 추린다. 상단에서 sentiment가 붙어 오면 그걸 믿고,
    없으면 keywords.looks_negative로 대체한다(중립 문서의 키워드 히트 오염 방지)."""
    picked = []
    for m in payload.mentions:
        if m.sentiment is not None:
            if str(m.sentiment).lower() in ("negative", "neg", "부정"):
                picked.append(m.text)
        elif keywords.looks_negative(m.text):
            picked.append(m.text)
    return picked


# 상단 탐지 1·2위 점수가 이만큼 안에 붙어 있으면 애매한 것으로 보고 두 상위의 자식을
# 모두 후보에 넣는다. 라벨셋으로 튜닝해야 하는 잠정값이다.
AMBIGUOUS_MARGIN = 0.15
# 상단 확신도가 이보다 낮으면 세부 판정이 아무리 확실해도 사람이 보게 한다.
UPSTREAM_REVIEW_BELOW = 0.5


def _upstream_pick(detection: str | dict[str, float]) -> tuple[list[str], float, float, list[str]]:
    """탐지 결과에서 (후보 상위 목록, 1위 점수, 1·2위 격차, 비고)를 뽑는다.

    팀 분류기는 멀티라벨이라 유형별 점수가 온다. 1위만 받아 확정하면 1·2위가 붙어 있을 때
    실제 애매함이 버려지므로, 격차가 작으면 두 상위를 모두 후보로 둔다.
    문자열 하나만 오면(단일 라벨) 점수를 알 수 없으므로 1.0으로 두되 비고에 남긴다.
    """
    if isinstance(detection, str):
        return [detection], 1.0, 1.0, ["탐지 점수가 제공되지 않아 상단 확신도를 알 수 없음"]

    ranked = sorted(detection.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        raise ValueError("탐지 결과가 비어 있습니다.")
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_score - second_score
    parents, notes = [top_type], []
    if len(ranked) > 1 and margin < AMBIGUOUS_MARGIN:
        parents.append(ranked[1][0])
        notes.append(
            f"탐지 1·2위 격차 {margin:.2f} < {AMBIGUOUS_MARGIN} - {ranked[1][0]}의 세부 유형도 후보에 포함"
        )
    return parents, float(top_score), float(margin), notes


def refine(
    payload: AlertPayload,
    detection: str | dict[str, float],
    allow_llm: bool = True,
) -> dict:
    """탐지 유형(상위) -> 세부 유형(하위) 판정.

    `detection`은 유형 코드 문자열 또는 {유형: 점수} 딕셔너리를 받는다. 딕셔너리를 주면
    상단 확신도와 1·2위 격차를 판정에 반영한다.

    **세부 판정의 확신도는 상단 확신도를 넘을 수 없다.** 후보가 하나뿐이라 고를 것이
    없더라도, 그건 "세부 판정에 불확실성이 없다"는 뜻이지 "유형이 확실하다"는 뜻이 아니다.
    상단이 틀렸으면 그 오류를 그대로 물려받으므로, 상단 확신도를 승계해야 한다.
    """
    parents, upstream_conf, margin, notes = _upstream_pick(detection)

    candidates: list[str] = []
    for parent in parents:
        children = risk_types.children_of(parent)
        if not children:
            raise KeyError(f"알 수 없는 탐지 유형: {parent}")
        candidates.extend(c for c in children if c not in candidates)

    def _wrap(result: dict) -> dict:
        # 세부 확신도와 상단 확신도 중 낮은 쪽을 최종값으로 쓴다.
        result["refine_confidence"] = result["confidence"]
        result["upstream_confidence"] = upstream_conf
        result["confidence"] = min(result["confidence"], upstream_conf)
        result["needs_review"] = (
            result["needs_review"]
            or upstream_conf < UPSTREAM_REVIEW_BELOW
            or len(parents) > 1
        )
        result["detection_parents"] = parents
        result["notes"] = notes + result.get("notes", [])
        return result

    if len(candidates) == 1:
        return _wrap({
            "risk_type": candidates[0],
            "confidence": 1.0,
            "route": "single",
            "reason": f"탐지 유형 {parents[0]}의 세부 유형이 하나뿐",
            "needs_review": False,
            "hit_counts": {},
            "notes": ["세부 판정 불확실성 없음 - 확신도는 상단에서 승계"],
        })

    texts = _negative_texts(payload)
    counts = {c: n for c, n in keywords.hit_counts(texts).items() if c in candidates}
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top_code, top_n = ranked[0]
    second_n = ranked[1][1] if len(ranked) > 1 else 0
    ratio = (top_n / second_n) if second_n else float("inf")

    # 경계 규칙 R1: R13(평판·루머)은 키워드로 확정하지 않는다. 루머의 '내용'은 다른
    # 유형의 어휘를 그대로 쓰기 때문에(예: "배송 다 밀렸다는 소문") 어휘로는 안 갈린다.
    # 실제 갈림길은 '사실로 확인됐는가'이고 그건 문맥 판단이다.
    if top_n >= MIN_TOP_HITS and ratio >= TOP_RATIO and top_code != "R13":
        return _wrap({
            "risk_type": top_code,
            "confidence": 0.9,
            "route": "keyword",
            "reason": f"후보 {len(candidates)}개 중 키워드 1위 {top_code} {top_n}건, 2위 대비 {ratio:.1f}배",
            "needs_review": False,
            "hit_counts": counts,
        })

    if not allow_llm:
        return _wrap({
            "risk_type": top_code,
            "confidence": 0.3,
            "route": "fallback",
            "reason": "LLM 세부 분류를 끈 상태라 키워드 1위를 사용(신뢰도 낮음)",
            "needs_review": True,
            "hit_counts": counts,
        })

    result = _refine_llm(payload, tuple(candidates), texts or [m.text for m in payload.mentions], counts)
    result["hit_counts"] = counts
    return _wrap(result)


def _refine_llm(
    payload: AlertPayload, candidates: tuple[str, ...], texts: list[str], counts: dict[str, int]
) -> dict:
    """후보를 제시하고 그 안에서만 고르게 한다. 대표 원문 5건 x 150자면 충분하다."""
    sample = [t[:150] for t in texts[:5]]
    hint = ", ".join(f"{c}:{n}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3])
    user_content = "\n".join(f"- {s}" for s in sample) + f"\n\n(참고: 키워드 히트 {hint})"

    schema = {
        "name": "risk_subtype_refinement",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "risk_type": {"type": "string", "enum": list(candidates)},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["risk_type", "confidence", "reason"],
            "additionalProperties": False,
        },
    }
    parsed, _usage = structured_call(
        system=_REFINE_PROMPT.format(
            company=payload.company_name,
            catalog=risk_types.catalog_for_prompt(candidates),
        ),
        user=user_content,
        schema=schema["schema"],
        schema_name="risk_subtype_refinement",
    )
    confidence = float(parsed["confidence"])
    return {
        "risk_type": parsed["risk_type"],
        "confidence": confidence,
        "route": "llm",
        "reason": parsed["reason"],
        "needs_review": confidence < 0.6,
    }


def classify(payload: AlertPayload, allow_llm: bool = True) -> dict:
    """반환: {risk_type, confidence, route, reason, needs_review, hit_counts}
    route는 'keyword' | 'llm' | 'fallback'. 문서 §11의 경로별 정확도 집계를 위해 남긴다."""
    texts = _negative_texts(payload)
    counts = keywords.hit_counts(texts)
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    top_code, top_n = ranked[0]
    second_n = ranked[1][1] if len(ranked) > 1 else 0
    ratio = (top_n / second_n) if second_n else float("inf")

    # 경계 규칙 R1: T11이 1위면 키워드로 확정하지 않는다(모듈 docstring 참고).
    keyword_decisive = (
        top_n >= MIN_TOP_HITS and ratio >= TOP_RATIO and top_code != "R13"
    )

    if keyword_decisive:
        return {
            "risk_type": top_code,
            "confidence": 0.9,
            "route": "keyword",
            "reason": f"키워드 사전 1위 {top_code} {top_n}건, 2위 대비 {ratio:.1f}배",
            "needs_review": False,
            "hit_counts": counts,
        }

    if not allow_llm:
        return {
            "risk_type": top_code,
            "confidence": 0.3,
            "route": "fallback",
            "reason": "LLM 분류를 끈 상태라 키워드 1위를 그대로 사용(신뢰도 낮음)",
            "needs_review": True,
            "hit_counts": counts,
        }

    result = _classify_llm(payload, texts or [m.text for m in payload.mentions], counts)
    result["hit_counts"] = counts
    return result


def _classify_llm(payload: AlertPayload, texts: list[str], counts: dict[str, int]) -> dict:
    """대표 원문 5건 x 150자만 보낸다 - 유형 판정에 원문 전량이 필요하지 않고,
    길게 넣을수록 비용만 늘고 판단이 흐려진다."""
    sample = [t[:150] for t in texts[:5]]
    hint = ", ".join(f"{c}:{n}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3])
    user_content = "\n".join(f"- {s}" for s in sample) + f"\n\n(참고: 키워드 히트 상위 {hint})"

    parsed, _usage = structured_call(
        system=_SYSTEM_PROMPT.format(
            company=payload.company_name, catalog=risk_types.catalog_for_prompt()
        ),
        user=user_content,
        schema=_SCHEMA["schema"],
        schema_name="risk_type_classification",
    )
    confidence = float(parsed["confidence"])
    return {
        "risk_type": parsed["risk_type"],
        "confidence": confidence,
        "route": "llm",
        "reason": parsed["reason"],
        # 문서 §3: 확신도가 기준 미만이면 검토 필요 플래그. 6단계에서 더 눈여겨보게 한다.
        "needs_review": confidence < 0.6,
    }
