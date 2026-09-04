"""4단계 보고서 초안 생성 (문서 §6).

수집된 근거만을 입력으로 LLM이 초안을 쓴다. 출력 형식은 strict JSON 스키마로 고정해
항목 누락과 표현 편차를 막는다(report_schema.py).

**프롬프트 조립 순서**: 시스템(역할 + 유형별 대응 원칙 + 주체별 지침 + 금지 규칙)
-> 사용자(맥락 + 정량 근거 + 원문 + 사례 + 법령). 원칙을 시스템 쪽에 두는 이유는
근거 텍스트가 길어져도 원칙이 뒤로 밀려 희석되지 않게 하기 위해서다.

**T3 등급의 3회 생성(self-consistency)**: 문서 §4대로 T3는 3회 독립 생성 후 합의한다.
지금 구현은 3개 초안을 모두 만들어 **검증을 통과한 것 중 첫 번째**를 채택하고 나머지를
후보로 남긴다 - 문장 단위 다수결은 자연어에서 신뢰하기 어려워, 합의 대신 '검증 통과'를
선별 기준으로 쓴다. 사람이 3안을 비교할 수 있게 후보를 버리지 않고 넘긴다.
"""
from __future__ import annotations

import json

from . import principles
from ._llm import response_model, structured_call
from .evidence import Evidence
from .report_schema import (
    MAX_CHECKLIST_HOURS,
    MAX_PRIMARY_RISKS,
    MAX_STRATEGIES,
    REPORT_SCHEMA,
)
from .risk_types import get
from .schema import AlertPayload


# 모델은 app.config의 response_model_name을 따른다(_llm.py). 이 저장소 기본값은
# gpt-5.6-luna이고 **temperature를 지원하지 않는다**(실측: 400 Unsupported parameter).
#
# 그래도 시나리오가 갈리는 이유: 시나리오는 같은 프롬프트를 반복 호출하는 방식이 아니라
# 관점(SCENARIO_STANCES)을 프롬프트에 명시해 방향을 벌린다. temperature 없이도 서로 다른
# 초안이 나오며, 오히려 같은 입력에 같은 결과가 나와 재현성이 좋아진다.

GENERATOR_VERSION = "generate-v0.1"

_SYSTEM_PROMPT = """당신은 기업 리스크 대응 보고서를 작성하는 위기관리 담당자입니다.
{company}에 대해 감지된 리스크의 **내부 실행용 대응 보고서 초안**을 작성하세요.
이 문서는 대외 공지문이 아니라 내부 검토용입니다.

[읽는 사람]
이 보고서는 **주가·IR 담당자와 마케팅·홍보 담당자**가 읽습니다. 데이터 분석가가 아닙니다.
따라서 다음을 지키세요.
- 지표의 내부 필드명(negative_ratio_7d, daily_growth_rate, anomaly_score 등)을 **본문에 쓰지
  마세요.** "부정적인 반응의 비율", "하루 언급량 증가 속도"처럼 뜻으로 풀어 쓰세요.
- 원문 식별자([m_1] 같은 표기)를 **본문에 쓰지 마세요.** 이건 시스템이 검증할 때만 쓰는
  내부 번호입니다. 인용한 원문은 cited_mention_ids 필드에만 넣고, 본문에서는 "한 판매자는
  ~라고 적었습니다"처럼 내용으로 지칭하세요.
- URL을 **본문에 쓰지 마세요.** 출처는 보고서 화면의 별도 인용 근거 영역에 자동으로
  표시됩니다. 본문에는 "보도에 따르면"처럼 출처의 성격만 밝히세요.
- 비교 수치를 쓸 때는 **무엇과 비교한 값인지 기간을 밝히세요.** 이 사안의 '평소' 기준은
  직전 {baseline_days}일 평균입니다. "평소 대비 4배"가 아니라 "직전 {baseline_days}일 평균
  대비 약 4배"처럼 쓰세요.

[이번 사안의 리스크 유형] {type_code} {type_label}
[1차 커뮤니케이션 대상] {stakeholder}

[위기 대응 공통 원칙 - 모든 유형에 적용]
{common_base}

[이 유형의 대응 원칙 - 반드시 따를 것]
{type_principle}

[대상별 커뮤니케이션 지침 - 반드시 따를 것]
{stakeholder_guide}
{rag_block}

[작성 규칙]
- 아래 [근거] 섹션에 주어진 내용만 사용하세요. 주어지지 않은 사실을 지어내지 마세요.
- scenario_headline은 이 관점을 가리키는 **짧은 이름**입니다. 12자 안팎으로, 담당자가
  탭에서 보고 무엇이 다른지 바로 알 수 있게 쓰세요. 예: "사실 확인 먼저", "선제적으로 알리기",
  "피해 구제 우선". 내부 용어나 영어를 쓰지 마세요.
- summary_points는 **요점 2~4개**입니다. 숫자를 쓰지 말고, 지금 무슨 일이 벌어지고 있으며
  왜 대응이 필요한지를 **쉬운 말로 설명**하세요. 수치는 judgment_basis에만 씁니다.
  예: "확인되지 않은 정보가 퍼지면서 사실관계보다 부정적 인식이 먼저 자리 잡을 수 있습니다."
  첫 항목은 상황을 한 문장으로 짚고, 나머지는 그래서 무엇이 중요한지로 이어 가세요.
- judgment_basis는 왜 이 사안을 리스크로 판단했는지 **수치를 들어** 서술합니다. 언급량·
  부정 비율·채널 수 같은 근거를 여기에 모으세요. 위 [읽는 사람] 규칙을 지키세요.
- 대응 전략은 최대 {max_strategies}개, 주 리스크는 최대 {max_primary}개입니다.
- 체크리스트 항목의 deadline_hours는 1 이상 {max_hours} 이하의 정수입니다.
- responsibility(책임 서사)를 '예방가능'으로 판단했다면 전략에 '부인_반박'을 쓰지 마세요.
- 전략의 target_stakeholder는 위에 명시된 1차 커뮤니케이션 대상과 일치시키세요.
- limitations(한계 고지)는 **2~3문장으로 짧게** 씁니다. "이 초안은 법률 자문이 아닙니다."를
  맨 앞에 두고, 그다음에 이번 사안에서 특히 확인이 필요한 것 한두 가지만 적으세요.
  일반론을 늘어놓지 마세요.
- monitoring_metrics는 **3개 이하**, 각 항목을 15자 안팎의 짧은 이름으로 씁니다.
  예: "부정 언급 비율", "고객센터 문의량", "해지 요청 건수". 설명 문장을 붙이지 마세요.
{case_rule}
{regulation_rule}
{stance_block}"""

_CASE_RULE_FORBIDDEN = (
    "- **과거 사례를 인용하지 마세요.** 현재 검증된 사례가 없습니다. 당신이 알고 있는\n"
    "  외부 사례를 언급하는 것도 금지입니다. cited_case_ids와 case_insights는 반드시\n"
    "  빈 배열이어야 합니다."
)
# 예전에는 사례를 case_insights에만 가뒀다. 그러면 근거를 모아 놓고도 전략에 반영되지
# 않는다 - 검증 규칙 9("사례를 하나도 활용하지 않음")가 실제로 걸린 적이 있다. 사례에서
# 확인된 대응 행동과 결과는 전략 설계에 쓰되, 남의 과거 일이 우리 현재 사실로 읽히지
# 않도록 서술 방식을 지정한다. 회사명·시점을 밝히는 것 자체는 막지 않는다 - 출처가
# 분명하면 오히려 근거가 된다.
_CASE_RULE_ALLOWED = (
    "- 과거 유사 사례에서 **실제로 확인된 대응 행동과 그 결과**는 전략과 체크리스트를\n"
    "  설계할 때 참고하세요. 다만 [근거]에 주어진 사례에 실제로 있는 행동만 쓰고, 기사에\n"
    "  없는 효과나 인과관계를 지어내지 마세요.\n"
    "- 전략 detail에는 사례의 줄거리를 옮기지 말고 **이번 사안에 할 행동**으로 바꿔 쓰세요.\n"
    "  나쁜 예: 'A사는 전량 회수했다'. 좋은 예: '유사 사례에서 효과가 확인된 전량 회수를\n"
    "  검토한다'. 사례의 회사명과 시점은 필요하면 밝히되, 과거 사례를 우리 회사가 이미\n"
    "  한 일처럼 서술하면 안 됩니다.\n"
    "- case_insights의 각 항목은 [근거]에 주어진 사례 하나에 대응합니다. case_id와\n"
    "  case_title을 그대로 옮기고, insight에는 **그 사례에서 얻은 교훈이 이번 사안에\n"
    "  어떻게 적용되는지**를 2~3문장으로 쓰세요. 사례 줄거리를 요약만 하지 마세요.\n"
    "- 인용한 사례의 case_id는 cited_case_ids에도 넣으세요. [근거]에 주어진 사례만\n"
    "  쓸 수 있으며, 당신이 알고 있는 외부 사례를 끌어오면 안 됩니다.\n"
    "- 사례는 사람이 검수하지 않은 웹 검색 자료입니다. 단정적으로 쓰지 말고 '보도에 따르면'\n"
    "  처럼 출처의 성격을 밝히고, 그 한계를 limitations에 한 줄 적으세요.\n"
    "- 출처 URL은 본문에도 case_insights에도 쓰지 마세요. 화면에 자동으로 표시됩니다."
)
_REG_RULE_NONE = (
    "- 적용 법령 정보가 제공되지 않았습니다. 특정 법령명이나 조항 번호, 법정 기한을 추정해서\n"
    "  쓰지 마세요. 법적 검토가 필요하다는 점만 limitations에 적으세요."
)
_REG_RULE_GIVEN = (
    "- [근거]의 적용 법령에 기한(deadline_hours)이 있는 항목은 **반드시** 체크리스트에\n"
    "  해당 항목을 만들어 넣으세요. 의무 사항은 선택지가 아닙니다."
)




# 시나리오 관점(stance). 같은 사안이라도 실제로 갈리는 선택지를 축으로 잡았다.
# 단순히 여러 번 생성해 다양성을 기대하는 방식은 비슷한 초안 3개를 만들 뿐이라,
# 무엇이 다른 선택인지를 프롬프트에 명시해 서로 다른 방향이 나오게 한다.
SCENARIO_STANCES: dict[str, str] = {
    "선제_공개": (
        "확인된 범위가 좁더라도 우리가 먼저 알리는 쪽을 택합니다. 외부가 먼저 보도했을 때보다 "
        "손상이 작다는 근거를 우선하되, 지금 아는 것과 모르는 것을 명확히 구분해 알립니다. "
        "감수하는 것: 초기 정보가 뒤에 수정될 수 있고 그 자체가 다시 지적받을 수 있습니다."
    ),
    "사실확인_우선": (
        "확인 절차를 먼저 밟아 한 번에 정확히 알리는 쪽을 택합니다. 다만 법정 통지·신고 기한이 "
        "있으면 그 기한은 절대 넘기지 않으며, 확인 중이라는 사실 자체는 즉시 알립니다. "
        "감수하는 것: 침묵 구간이 길어지면 은폐로 읽힐 수 있고 그사이 추측이 확산됩니다."
    ),
    "피해구제_중심": (
        "원인 규명과 별개로 피해 구제와 보상 기준을 앞세우는 쪽을 택합니다. 책임 소재를 다투기 "
        "전에 피해자가 지금 받을 수 있는 것을 먼저 제시합니다. "
        "감수하는 것: 책임을 인정한 것으로 해석될 수 있어 법무 검토가 함께 필요합니다."
    ),
}

DEFAULT_STANCES = ("선제_공개", "사실확인_우선", "피해구제_중심")

_STANCE_BLOCK = """
[이번 초안의 관점]
같은 사안에도 실무에서 갈리는 선택이 있습니다. 이 초안은 아래 관점을 택한 안입니다.
{stance_text}

이 관점을 전략과 체크리스트 전반에 일관되게 반영하세요. 다만 위 [대응 원칙]과 충돌하면
원칙이 우선이며, 법정 기한이 있는 의무는 어떤 관점에서도 지켜야 합니다.
scenario_stance에는 "{stance_key}"를 그대로 넣고, scenario_tradeoff에는 이 관점을 택했을 때
무엇을 감수하는지 한 문장으로 적으세요."""

# 이 블록을 '참고용'이라 부르면 모델이 읽고 흘린다. 매뉴얼·가이드에서 찾아온 구체적인
# 조치를 실제 전략과 체크리스트에 넣게 하려는 자료이므로 '대응 근거'로 부른다. 원칙이
# 가드레일이라는 순서는 그대로다 - 자료가 원칙과 어긋나면 원칙을 따른다.
_RAG_HEADER = (
    "\n[상황별 대응 근거]\n"
    "아래는 대응 매뉴얼·가이드에서 이번 상황과 가까운 대목을 찾아온 것입니다. "
    "이번 상황에 직접 적용할 수 있는 구체적인 조치가 있으면 전략과 체크리스트에 반영하세요.\n"
    "- 위 [대응 원칙]과 충돌하면 원칙이 우선입니다.\n"
    "- 자료에 없는 사실이나 효과를 덧붙이지 마세요.\n"
    "- 각 항목에 붙은 한계와 적용 범위를 지키세요. 다른 나라 절차를 다룬 자료라면\n"
    "  국내 절차로 그대로 옮기지 말고 판단 근거로만 쓰세요.\n"
)


def build_system_prompt(
    risk_type_code: str,
    payload: AlertPayload,
    ev: Evidence,
    provider=None,
    situation: str = "",
    stance: str | None = None,
) -> str:
    """provider를 주면 상황별 보충 지침(RAG)이 프롬프트에 덧붙는다.

    보충은 '있으면 좋은 것'이라 실패해도 빈 문자열이 되고, 정적 원칙만으로 프롬프트가
    완성된다. 원칙이 가드레일이고 보충은 살일 뿐이므로 이 순서가 뒤바뀌면 안 된다.
    """
    rt = get(risk_type_code)
    rag_block = ""
    if provider is not None and hasattr(provider, "render_supplements"):
        rendered = provider.render_supplements(risk_type_code, situation or _situation_of(payload, ev))
        if rendered:
            rag_block = _RAG_HEADER + rendered

    principle = (
        provider.principle_for(rt.code) if provider is not None
        else principles.principle_for(rt.code)
    )
    guide = (
        provider.guide_for(rt.stakeholder) if provider is not None
        else principles.guide_for(rt.stakeholder)
    )
    return _SYSTEM_PROMPT.format(
        company=payload.company_name,
        type_code=rt.code,
        type_label=rt.label,
        stakeholder=rt.stakeholder.value,
        type_principle=principle,
        stakeholder_guide=guide,
        rag_block=rag_block,
        common_base=principles.common_base(),
        max_strategies=MAX_STRATEGIES,
        max_primary=MAX_PRIMARY_RISKS,
        max_hours=MAX_CHECKLIST_HOURS,
        baseline_days=payload.baseline_window_days,
        case_rule=_CASE_RULE_FORBIDDEN if ev.no_case_mode else _CASE_RULE_ALLOWED,
        regulation_rule=_REG_RULE_NONE if ev.no_regulation_mode else _REG_RULE_GIVEN,
        stance_block=(
            _STANCE_BLOCK.format(stance_key=stance, stance_text=SCENARIO_STANCES[stance])
            if stance in SCENARIO_STANCES else ""
        ),
    )


def _situation_of(payload: AlertPayload, ev: Evidence) -> str:
    """보충 검색에 쓸 상황 문장. 실제 반응에서 뽑아야 지금 국면에 맞는 것이 검색된다."""
    parts = [payload.company_name]
    parts.extend(m.text[:120] for m in ev.mentions[:3])
    return " ".join(parts)


# 모델 내부 피처명 -> 사람이 읽는 이름. 보고서를 읽는 사람은 주가·마케팅 담당자라
# 필드명을 그대로 노출하면 해석이 막힌다(프롬프트의 [읽는 사람] 규칙과 짝을 이룬다).
# 모르는 피처명이 오면 그대로 두되, 그런 이름이 보고서에 보이면 사전에 추가할 신호다.
_FEATURE_LABELS = {
    "negative_ratio_7d": "부정적 반응 비율(7일)",
    "negative_ratio": "부정적 반응 비율",
    "daily_growth_rate": "하루 언급량 증가 속도",
    "mention_count": "언급량",
    "anomaly_score": "평소와 다른 정도(이상 점수)",
    "n_unique_channels": "서로 다른 채널 수",
    "diversity_ratio": "작성자 다양성",
    "video_hhi": "특정 채널 쏠림 정도",
}


def _feature_label(feature: str) -> str:
    return _FEATURE_LABELS.get(feature, feature)


def _fmt_num(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "미상"
    return f"{v * 100:.1f}%" if pct else (f"{v:.4g}")


def build_user_prompt(payload: AlertPayload, ev: Evidence) -> str:
    """[맥락] + [정량 근거] + [원문] + [사례] + [법령] 순으로 근거 블록을 조립한다."""
    parts: list[str] = []

    ctx = [f"기업: {payload.company_name}"]
    if payload.industry:
        ctx.append(f"업종: {payload.industry}")
    if payload.main_services:
        ctx.append(f"주요 서비스: {payload.main_services}")
    if payload.window_start or payload.window_end:
        ctx.append(f"관측 기간: {payload.window_start or '?'} ~ {payload.window_end or '?'}")
    if payload.days_since_last_alert is not None:
        ctx.append(
            f"직전 경보로부터 {payload.days_since_last_alert}일 경과"
            + (f" (직전 유형 {payload.last_risk_type})" if payload.last_risk_type else "")
        )
    parts.append("[맥락]\n" + "\n".join(f"- {c}" for c in ctx))

    quant = []
    bw = payload.baseline_window_days
    if payload.mention_count is not None:
        base = f" (직전 {bw}일 평균 {_fmt_num(payload.baseline_mean)}건)" if payload.baseline_mean else ""
        quant.append(f"기간 내 언급량 {payload.mention_count}건{base}")
    if payload.negative_ratio is not None:
        base = (
            f" (직전 {bw}일 평균 {_fmt_num(payload.negative_ratio_baseline, pct=True)})"
            if payload.negative_ratio_baseline is not None
            else ""
        )
        quant.append(f"부정적 반응 비율 {_fmt_num(payload.negative_ratio, pct=True)}{base}")
    if payload.n_unique_channels is not None:
        quant.append(f"이야기가 나온 서로 다른 채널 수 {payload.n_unique_channels}개")
    if payload.source_mix:
        mix = ", ".join(f"{k} {v * 100:.0f}%" for k, v in payload.source_mix.items())
        quant.append(f"어디서 나왔는지: {mix}")
    for a in ev.attribution:
        line = f"{_feature_label(a.feature)}: 현재 {_fmt_num(a.value)}"
        if a.baseline is not None:
            line += f" / 직전 {bw}일 평균 {_fmt_num(a.baseline)}"
        if a.percentile is not None:
            line += f" / 과거 이력에서 상위 {100 - a.percentile:.1f}% 수준"
        quant.append(line)
    parts.append("[정량 근거]\n" + ("\n".join(f"- {q}" for q in quant) if quant else "- (제공된 지표 없음)"))

    if ev.mentions:
        lines = []
        for m in ev.mentions:
            meta = [m.source or "?", m.published_at or "?"]
            if m.like_count is not None:
                meta.append(f"공감 {m.like_count}")
            if m.pick_reason:
                meta.append(m.pick_reason)
            # 상류(service._payload_from_event)에서 이미 600자로 자른다. 여기서 300으로
            # 또 줄이면 사건 경위 뒷부분이 한 번 더 잘린다. 상류 상한에 맞춘다.
            lines.append(f"- [{m.mention_id}] ({' / '.join(meta)}) {m.text[:600]}")
        parts.append("[원문 - 인용 시 대괄호 안 id를 cited_mention_ids에 넣을 것]\n" + "\n".join(lines))
    else:
        parts.append("[원문]\n- (선별된 원문 없음. 원문 인용 없이 정량 근거만으로 작성할 것)")

    if ev.cases:
        lines = []
        for c in ev.cases:
            tag = " ※웹 검색 수집·미검수" if c.provenance == "web_search" else ""
            lines.append(
                f"- [{c.case_id}] {c.title} ({c.risk_type} / 결과: {c.outcome}){tag}\n"
                f"  무슨 일: {c.summary_what}\n"
                f"  대응: {c.summary_response}\n"
                f"  결과: {c.summary_result}\n"
                f"  교훈: {c.lesson}\n"
                f"  출처: {', '.join(c.source_urls)}"
            )
        parts.append("[과거 유사 사례 - 인용 시 case_id를 cited_case_ids에 넣을 것]\n" + "\n".join(lines))

    if ev.regulations:
        duties, upcoming, baseline = [], [], []
        for r in ev.regulations:
            dl = f" / 기한 {r.deadline_hours}시간" if r.deadline_hours else ""
            line = f"- {r.law_name} {r.article}: {r.requirement[:160]}{dl}"
            if r.applicability_note:
                line += (
                    "\n    ※ 적용 요건: " + r.applicability_note
                    + " — 해당 여부를 먼저 확인하고, 확인 전이면 단정적으로 쓰지 마세요."
                )
            if not r.is_upcoming and not r.checklist_enforce:
                baseline.append(line)
                continue
            (upcoming if r.is_upcoming else duties).append(
                line + (f" (시행 예정일 {r.effective_from})" if r.is_upcoming else "")
            )
        if duties:
            parts.append("[적용 법령 - 기한 있는 항목은 체크리스트에 반드시 포함]\n" + "\n".join(duties))
        if baseline:
            parts.append(
                "[관련 법정 기준 - 위반 여부를 판정하는 기준선]\n"
                + "\n".join(baseline)
                + "\n72시간 체크리스트 항목이 아니라, 지금 상황이 이 기준을 넘었는지 판단하고"
                  " 보전 기준을 세우는 근거로 쓰세요."
            )
        if upcoming:
            parts.append(
                "[시행 예정 법령 - 아직 의무가 아님. 권고 사항으로만 다룰 것]\n"
                + "\n".join(upcoming)
                + "\n지금 지켜야 할 의무로 쓰지 말고, 곧 시행되니 미리 준비하라는 취지로만 언급하세요."
            )

    quality = []
    if payload.n_mentions_7d is not None:
        quality.append(f"최근 7일 표본 {payload.n_mentions_7d}건")
    if payload.baseline_days is not None:
        quality.append(f"기준선 확보 기간 {payload.baseline_days}일")
    if payload.is_warmup:
        quality.append("표본 부족 구간(warmup) - 비율 수치를 단정적으로 쓰지 말 것")
    if quality:
        parts.append("[데이터 품질 - limitations에 반영할 것]\n" + "\n".join(f"- {q}" for q in quality))

    return "\n\n".join(parts)


def generate(
    payload: AlertPayload,
    risk_type_code: str,
    ev: Evidence,
    n: int = 1,
    provider=None,
) -> tuple[list[dict], dict[str, int]]:
    """초안 n개를 만든다. 반환: (초안 리스트, 토큰 사용량).

    n>1은 같은 프롬프트를 반복 호출한다. 이 저장소의 응답 모델은 temperature를 지원하지
    않아 결과가 거의 같게 나오므로, 다양성이 필요하면 generate_scenarios를 쓸 것.
    """
    system = build_system_prompt(risk_type_code, payload, ev, provider=provider)
    user = build_user_prompt(payload, ev)

    drafts: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    for _ in range(n):
        draft, call_usage = structured_call(
            system=system, user=user,
            schema=REPORT_SCHEMA["schema"], schema_name="crisis_response_report",
        )
        drafts.append(draft)
        for k in usage:
            usage[k] += call_usage.get(k, 0)

    return drafts, usage


def generate_scenarios(
    payload: AlertPayload,
    risk_type_code: str,
    ev: Evidence,
    stances: tuple[str, ...] = DEFAULT_STANCES,
    provider=None,
) -> tuple[list[dict], dict[str, int]]:
    """관점이 다른 초안을 관점 수만큼 만든다.

    같은 프롬프트를 여러 번 부르는 self-consistency와는 목적이 다르다. 그쪽은 같은 답이
    나오는지 확인하는 것이고, 이쪽은 **실무에서 실제로 갈리는 선택지**를 나란히 놓아
    담당자가 고르게 하는 것이다. 그래서 관점을 프롬프트에 명시해 방향을 벌린다.
    """
    user = build_user_prompt(payload, ev)

    drafts: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    for stance in stances:
        system = build_system_prompt(risk_type_code, payload, ev, provider=provider, stance=stance)
        draft, call_usage = structured_call(
            system=system, user=user,
            schema=REPORT_SCHEMA["schema"], schema_name="crisis_response_report",
        )
        draft.setdefault("scenario_stance", stance)
        drafts.append(draft)
        for k in usage:
            usage[k] += call_usage.get(k, 0)
    return drafts, usage


def _draft_signature(draft: dict) -> str:
    """중복 판정에 쓸 대표 텍스트. 요약과 전략 제목이 방향을 가장 잘 드러낸다."""
    parts = list(draft.get("summary_points") or [])
    parts += [str(s.get("title", "")) for s in draft.get("strategies") or []]
    parts += [str(s.get("strategy_type", "")) for s in draft.get("strategies") or []]
    return " ".join(parts)


def dedupe_scenarios(
    drafts: list[dict], threshold: float = 0.93
) -> tuple[list[dict], list[str], dict]:
    """내용이 사실상 같은 시나리오를 하나로 접는다.

    관점을 벌려도 사안에 따라 결론이 하나로 모이는 경우가 있다. 그때 거의 같은 초안 셋을
    나란히 보여주면 담당자가 차이를 찾느라 시간만 쓴다. 요약·전략을 임베딩해 코사인
    유사도가 threshold를 넘으면 뒤에 온 것을 접고, 접힌 관점을 앞 시나리오에 기록한다.

    임베딩을 못 쓰면(색인 인프라 없음·API 실패) 접지 않고 그대로 둔다 - 잘못 접는 것보다
    중복이 남는 편이 낫다.

    반환의 세 번째 값은 임베딩 사용량이다. 호출자가 집계에 더하지 않으면 이 호출이
    사용량 보고에서 통째로 빠진다.
    """
    if len(drafts) <= 1:
        return drafts, [], {}
    try:
        import numpy as np

        from .rag.embed import embed

        vectors, embed_usage = embed([_draft_signature(d) for d in drafts])
    except Exception as exc:
        return drafts, [f"유사도 판정 불가 - 중복 접기 생략: {str(exc)[:80]}"], {}

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = vectors / np.clip(norms, 1e-9, None)
    kept: list[dict] = []
    kept_idx: list[int] = []
    notes: list[str] = []
    for i, draft in enumerate(drafts):
        merged_into = None
        for pos, j in enumerate(kept_idx):
            if float(unit[i] @ unit[j]) >= threshold:
                merged_into = pos
                break
        if merged_into is None:
            kept.append(draft)
            kept_idx.append(i)
        else:
            host = kept[merged_into]
            host.setdefault("merged_stances", []).append(draft.get("scenario_stance", "?"))
            notes.append(
                f"{draft.get('scenario_stance','?')} 관점이 "
                f"{host.get('scenario_stance','?')}와 사실상 같아 하나로 합침"
            )
    return kept, notes, embed_usage


def regenerate_with_feedback(
    payload: AlertPayload,
    risk_type_code: str,
    ev: Evidence,
    previous: dict,
    violations: list[str],
    provider=None,
) -> tuple[dict, dict[str, int]]:
    """자동 검증 실패 시 **위반 항목을 지정해** 1회 재생성한다(문서 §6).

    통째로 다시 쓰게 하지 않고 무엇이 틀렸는지 알려주는 이유: 통과한 부분까지 매번 새로
    쓰면 고쳐야 할 곳 말고 다른 데가 바뀌어, 두 번째 검증에서 새 위반이 생기는 일이 잦다.
    """
    # 재생성 결과는 원래 관점 이름으로 저장되므로 stance를 그대로 넘겨야 한다. 빼먹으면
    # "선제_공개"라고 적힌 시나리오가 관점 지침 없이 만들어진다.
    system = build_system_prompt(
        risk_type_code, payload, ev, provider=provider,
        stance=previous.get("scenario_stance"),
    )
    user = build_user_prompt(payload, ev)

    # 재생성은 지정된 위반만 고치는 작업이라 다양성이 필요 없다. 재현성 쪽으로 붙인다.

    feedback = (
        "직전 초안이 아래 검증 규칙을 위반했습니다. 해당 부분만 고치고 나머지는 유지하세요.\n"
        + "\n".join(f"- {v}" for v in violations)
        + "\n\n[직전 초안]\n"
        + json.dumps(previous, ensure_ascii=False)
    )

    retried, usage = structured_call(
        system=system,
        user=user + "\n\n" + feedback,
        schema=REPORT_SCHEMA["schema"],
        schema_name="crisis_response_report",
    )
    return retried, usage
