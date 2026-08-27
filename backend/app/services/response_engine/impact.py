"""동종 기업 이슈가 우리 기업에 영향을 미치는지 판단하는 단계 (동종 기업 경로 전용).

메인 기업 경로가 "무엇이 문제인가(유형) → 어떻게 대응하나(방안)"라면, 동종 기업 경로는
"남의 일이 우리에게 오는가(영향) → 그렇다면 무엇을 하나(추천)"다. 이 모듈은 앞의 절반,
즉 **영향 판단까지만** 담당한다. 추천 생성은 아직 구현하지 않았다.

설계에서 원안과 다르게 잡은 세 가지:

1. **유형을 먼저 확정하지 않고, 영향 판단과 한 번에 받는다.**
   원안은 동종 기업이면 유형 분류를 건너뛰고 바로 영향을 묻는 흐름이었다. 그런데 유형을
   모르면 영향 판단의 근거가 약해진다 - 동종사의 개인정보 유출(T8)은 같은 규제를 받는
   우리도 점검 대상이 되지만, 동종사의 배송 지연(T2)은 오히려 반사이익일 수 있어서
   방향 자체가 갈린다. 그렇다고 유형 분류에 LLM을 한 번 더 쓰면 대다수가 '영향 없음'으로
   버려질 건에 비용을 두 번 쓰게 된다. 그래서 **키워드 1차 분류(무료)까지만 돌려 잠정
   유형을 힌트로 넘기고, LLM이 유형과 영향을 한 호출에서 함께 확정**한다.

2. **'영향 있음/없음' 이분법 대신 방향을 구분한다.**
   동종사 위기가 우리에겐 기회인 경우가 실제로 있다(경쟁사 배송 사고 → 고객 유입).
   이걸 '영향 없음'으로 처리하면 마케팅 담당자가 알아야 할 상황을 놓친다. 게다가 반사이익
   국면에서 공격적 마케팅은 역풍을 부르기 쉬워, 그 자체가 추천 대상이다.

3. **영향의 '경로'를 유형화해 남긴다.**
   "영향 있음"만으로는 추천을 만들 수 없다. 규제 확대로 오는지, 소비자 인식 전이로 오는지,
   투자자 질의로 오는지에 따라 할 일이 완전히 달라진다. impact_channels가 다음 단계
   (추천 생성)의 입력이 된다.
"""
from __future__ import annotations

from . import keywords, risk_types
from ._llm import structured_call
from .schema import AlertPayload
IMPACT_VERSION = "impact-v0.1"

# 영향 방향. '영향_없음'이면 추천 단계로 넘어가지 않는다.
IMPACT_DIRECTIONS = ("부정적_파급", "반사이익", "영향_없음")
IMPACT_LEVELS = ("높음", "중간", "낮음", "없음")

# 영향이 우리에게 오는 경로. 추천 생성이 이 값을 보고 무엇을 권할지 정한다.
IMPACT_CHANNELS = (
    "규제_조사_확대",      # 업계 전반 점검·제재로 번짐
    "소비자_인식_전이",    # "이 업계는 다 그렇다"는 인식 확산
    "투자자_주가_동조",    # 섹터 전반 투자심리·질의 확대
    "동일_취약점_보유",    # 우리도 같은 구조·정책을 갖고 있어 재현 가능
    "공급망_협력사_공유",  # 같은 협력사·물류망을 써서 직접 연결됨
    "고객_유입_기회",      # 이탈 고객이 우리로 유입 (반사이익 경로)
)

# 영향 판단은 보수적으로 - 애매하면 사람이 보게 한다.
NEEDS_REVIEW_BELOW = 0.6

_SCHEMA = {
    "name": "peer_impact_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "risk_type": {"type": "string", "enum": list(risk_types.CODES)},
            "impact_direction": {"type": "string", "enum": list(IMPACT_DIRECTIONS)},
            "impact_level": {"type": "string", "enum": list(IMPACT_LEVELS)},
            "impact_channels": {
                "type": "array",
                "items": {"type": "string", "enum": list(IMPACT_CHANNELS)},
            },
            "reason": {"type": "string"},
            "watch_points": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": [
            "risk_type", "impact_direction", "impact_level",
            "impact_channels", "reason", "watch_points", "confidence",
        ],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = """당신은 기업 리스크 모니터링 분석가입니다.
**동종 기업인 {peer_company}에서 발생한 이슈**가 **우리 기업인 {main_company}에 영향을 미치는지**
판단하세요. 이 판단의 대상은 동종 기업의 대응이 아니라, 우리 기업이 받게 될 파급입니다.

[우리 기업] {main_company}{main_context}
[이슈 발생 기업] {peer_company}{peer_context}

[리스크 유형 목록 - 이 이슈가 어디에 해당하는지도 함께 고르세요]
{catalog}

[판단 규칙]
- impact_direction은 셋 중 하나입니다.
  · 부정적_파급 : 이 이슈로 우리 기업이 불이익을 받을 수 있다
  · 반사이익   : 이 이슈로 우리 기업이 상대적으로 이득을 볼 수 있다
  · 영향_없음   : 동종 업계에서 벌어진 일이지만 우리 기업과 실질적 연결고리가 없다
- **'같은 업계라서'만으로 영향 있음으로 판단하지 마세요.** 우리 기업에 실제로 닿는 경로가
  설명되어야 합니다. 경로를 못 대겠으면 영향_없음이 맞습니다.
- impact_channels에는 영향이 우리에게 오는 경로를 고르세요(복수 가능).
  영향_없음이면 빈 배열입니다.
  · 규제_조사_확대   : 감독기관이 업계 전반으로 점검·제재를 넓힐 수 있는 사안
  · 소비자_인식_전이 : 개별 기업 문제가 업계 전체 불신으로 번지는 사안
  · 투자자_주가_동조 : 섹터 투자심리나 IR 질의가 함께 움직이는 사안
  · 동일_취약점_보유 : 우리도 같은 구조·정책·시스템을 갖고 있어 재현될 수 있는 사안
  · 공급망_협력사_공유 : 같은 협력사·물류망·결제사를 공유해 직접 연결되는 사안
  · 고객_유입_기회   : 이탈 고객이 우리 쪽으로 올 수 있는 사안(반사이익 경로)
- impact_level은 우리 기업이 받을 영향의 크기입니다. 영향_없음이면 '없음'입니다.
- watch_points에는 앞으로 무엇을 지켜봐야 하는지 2~4개 적으세요. 우리 기업 기준으로,
  구체적인 관찰 대상을 쓰세요. 영향_없음이면 빈 배열입니다.
- reason은 한국어 2~3문장입니다. 왜 그렇게 판단했는지, 특히 **어떤 경로로 닿는지**를
  적으세요. 지표 필드명이나 원문 번호는 쓰지 마세요.
- confidence는 0.0~1.0입니다. 원문이 적거나 판단이 갈리면 낮게 주세요."""




def _context_line(name: str | None, industry: str | None, services: str | None) -> str:
    bits = [b for b in (industry, services) if b]
    return f" ({' · '.join(bits)})" if bits else ""


def analyze(payload: AlertPayload, top_k_texts: int = 6) -> dict:
    """동종 기업 이슈의 우리 기업 영향도를 판단한다.

    반환: {risk_type, impact_direction, impact_level, impact_channels, reason,
           watch_points, confidence, needs_review, keyword_hint, proceed}
    `proceed`가 True일 때만 다음 단계(추천 생성)로 넘어간다.
    """
    if not payload.main_company_name:
        raise ValueError(
            "동종 기업 경로에는 main_company_name(우리 기업)이 필요합니다. "
            "페이로드에 main_company_name 또는 my_company를 넣으세요."
        )

    # 키워드 1차 분류(무료)로 잠정 유형을 뽑아 힌트로 넘긴다 - 모듈 docstring의 설계 근거 1.
    texts = [m.text for m in payload.mentions]
    counts = keywords.hit_counts(texts)
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    hint = ", ".join(f"{c}:{n}" for c, n in ranked[:3] if n > 0) or "없음"

    sample = "\n".join(f"- {m.text[:200]}" for m in payload.mentions[:top_k_texts]) or "- (원문 없음)"
    user_content = (
        f"[{payload.company_name}에서 관측된 반응]\n{sample}\n\n"
        f"(참고: 키워드 사전 히트 상위 {hint})"
    )

    parsed, call_usage = structured_call(
        system=_SYSTEM_PROMPT.format(
            peer_company=payload.company_name,
            main_company=payload.main_company_name,
            main_context=_context_line(
                payload.main_company_name,
                payload.main_company_industry,
                payload.main_company_services,
            ),
            peer_context=_context_line(
                payload.company_name, payload.industry, payload.main_services
            ),
            catalog=risk_types.catalog_for_prompt(),
        ),
        user=user_content,
        schema=_SCHEMA["schema"],
        schema_name="peer_impact_analysis",
    )
    confidence = float(parsed["confidence"])
    direction = parsed["impact_direction"]

    result = dict(parsed)
    result["confidence"] = confidence
    result["needs_review"] = confidence < NEEDS_REVIEW_BELOW
    result["keyword_hint"] = counts
    # 영향_없음이면 여기서 끝난다. 추천 생성으로 넘기지 않는 게 이 경로의 비용 통제 지점이다.
    result["proceed"] = direction != "영향_없음"
    result["usage"] = call_usage
    return result
