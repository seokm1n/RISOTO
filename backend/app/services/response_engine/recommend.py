"""동종 기업 추천방안 생성 - impact.py가 '영향 있음'으로 판단한 지점을 이어받는다.

impact.py의 모듈 docstring이 말하는 "뒤의 절반"이 이 파일이다: 영향 판단(앞 절반)이
방향·경로·관찰 지점을 확정하면, 여기서 담당자가 바로 실행할 수 있는 권고를 만든다.
입력은 service._build_peer_content가 조립한 peer dict(영향 판단 결과 + 사례 + 법령)다.

**메인 경로의 generate.py를 베끼지 않는 이유**(원 인수인계 지시): 동종 기업 담당자는 자기
회사에 사고가 난 게 아니다. 필요한 것은 대응 전략 전문이 아니라 "우리도 위험한가, 뭘
확인해야 하나"에 대한 짧은 답이다. 그래서 체크리스트·시나리오·입장문 초안이 없다.

**앞 단계 결과를 다시 만들지 않는다.** impact_direction·impact_level·impact_channels·
watch_points·cases는 이미 채워져 들어온다. 이 모듈은 그것들을 근거로 행동만 만든다.

**한 사건에서 기회와 위험이 동시에 온다.** 실측 사례: 쿠팡 정산 이슈에서 방향은
반사이익인데 채널에 고객_유입_기회와 규제_조사_확대가 같이 나왔다. 그래서 방향에
따라 리포트 형태를 통째로 가르지 않고, **채널마다 권고를 매단다**. 방향으로 갈랐다면
반사이익 케이스에서 규제 대응이 통째로 빠졌을 것이다.

이식 메모(2026-08-27): 독립 작업본(risoto_response_handoff/response/recommend.py)에서
가져오며 세 가지를 이 저장소 방식으로 바꿨다 - ①dotenv/chat.completions ->
_llm.structured_call(Responses API, temperature 미지원 모델이라 관련 로직 제거)
②법령 시드 dict -> retrieval.KoreanRegulationMapper가 조회한 값을 service가 주입
③검증 6규칙·재생성 계약은 그대로(팀 합의: docs/reply_undecided_items.md 회신 O).
"""
from __future__ import annotations

import json
import re

from ._llm import structured_call
from .recommend_schema import (
    MAX_AVOID,
    MAX_RECOMMENDATIONS,
    RECOMMEND_SCHEMA,
)

# 법령 매핑 소스를 시드 dict에서 KoreanRegulationMapper 조회로 바꾼 것이 v0.1과의
# 실질적 거동 차이라 버전을 올린다 (예: R11 정산은 v0.1에서 법령 블록이 없었다).
RECOMMENDER_VERSION = "recommend-v0.2"


_SYSTEM_PROMPT = """당신은 기업 리스크 담당자를 돕는 위기관리 어드바이저입니다.
{peer_company}에서 발생한 사안이 {main_company}에 미칠 영향에 대해, {main_company}의
브랜드·리스크 담당자가 **바로 실행할 수 있는 권고**를 작성하세요.
두 필드는 역할이 다릅니다. **source_event는 상황, headline은 행동입니다.**
- source_event에는 {peer_company}에서 무슨 일이 있었는지를 1~2문장으로 적고, 이것이
  {main_company} 자체 사고가 아니라는 점을 여기서 밝히세요. 사안을 두루뭉술하게
  "{peer_company} 사안"이라고 쓰지 말고 무엇에 관한 것인지 명시하세요.
  담당자가 "그래서 저쪽에 뭐가 터진 거지"를 다시 찾지 않아도 되게 구체적으로 쓰세요.
- headline에는 recommendations 전체를 **한 문장의 행동 요약**으로 압축하세요.
  담당자가 이 한 줄만 읽고도 무엇부터 하면 되는지 알 수 있어야 합니다.
- **headline에 "{main_company} 사고 아님"류의 문장을 넣지 마세요.** 그 해소는
  source_event가 이미 했습니다. 바로 위에서 읽은 말을 다시 읽게 하지 마세요.

[중요 - 이 상황의 성격]
{main_company}에서 사고가 난 것이 아닙니다. 다른 회사({peer_company})의 사안이 업계로
번지면서 {main_company}가 함께 언급되고 있는 상황입니다. 담당자는 경보만 보고 자기
회사 사고로 오해할 수 있으므로, source_event에서 이 점을 가장 먼저 해소하세요.

[읽는 사람]
브랜드·홍보·리스크 담당자입니다. 시장 분석가가 아닙니다.
- 업계 구조 분석이나 시장 전망을 쓰지 마세요. "이커머스 정산 관행이 변화하고 있다" 같은
  문장은 이 사람의 업무 언어가 아니고 할 일이 나오지 않습니다.
- 내부 지표 필드명(negative_ratio, anomaly_score 등)을 본문에 쓰지 마세요.
- URL을 본문에 쓰지 마세요. 출처는 화면의 별도 영역에 표시됩니다.

[이번 사안]
- 영향 방향: {impact_direction}
- 노출 수준: {impact_level}
- 판단 근거: {reason}

[작성 규칙]
- recommendations의 각 항목은 **아래 [영향 경로] 중 하나에 반드시 매달아야 합니다.**
  경로에 없는 내용을 권고하지 마세요. channel에는 경로 이름을 그대로 적으세요.
- recommendations는 최대 {max_recommendations}개입니다. 실행 가능한 것만 남기세요.
- **확인하지 않은 것을 단정하지 마세요.** {main_company}의 내부 상태(보안 체계, 정산
  주기 준수율, 계약 조건 등)는 알 수 없습니다. 그런 항목은 action을 "확인한다"로 쓰고
  verify_first를 true로 두세요. 확인 전에 대외 답변하는 것이 이 상황의 대표적 사고입니다.
- owner_hint에는 그 일을 실제로 할 부서를 적으세요(예: "보안팀에 확인 요청").
  담당자 본인이 하는 일과 남에게 요청할 일이 구분되어야 합니다.
- avoid는 최대 {max_avoid}개입니다. 이번 사안에서 하면 안 되는 말이나 행동을 적으세요.
- realert_condition은 **관찰이 아니라 기준선**입니다. "정산 민원을 지켜본다"가 아니라
  "규제기관이 오픈마켓 정산 실태점검을 공식 발표하면"처럼 판정 가능하게 쓰세요.
- limitations에는 이 문서가 내부 검토용이며 검수되지 않은 수집 자료가 섞여 있을 수
  있다는 점을 넣으세요. 법률 자문이 아니라는 점도 포함하세요.
{direction_rule}
{case_rule}"""


# 반사이익은 다루기 까다롭다. "경쟁사 악재로 고객을 유치할 기회"라는 문장이 외부로
# 새면 그 자체가 새 리스크가 된다. 그래서 기회를 '경쟁사 손실 활용'이 아니라
# '선점 가능한 개선·홍보 항목'으로 프레이밍하도록 지시한다.
_DIRECTION_RULE_UPSIDE = (
    "- 이 사안은 {main_company}에 **반사이익** 방향입니다. 다만 기회를 "
    "'경쟁사 사고로 생긴 이탈 고객 유치'로 쓰지 마세요. 이 문서가 외부로 새면 그 문장\n"
    "  자체가 평판 리스크가 됩니다. '우리가 먼저 갖춰 알릴 수 있는 항목'으로 쓰세요.\n"
    "- avoid에 **경쟁사 사고를 직접 언급하는 마케팅·홍보 금지**를 반드시 포함하세요.\n"
    "- 반사이익이어도 같은 사안에서 위험이 함께 올 수 있습니다. [영향 경로]에 규제·조사\n"
    "  성격의 경로가 있으면 그쪽 권고를 빠뜨리지 마세요."
)
_DIRECTION_RULE_DOWNSIDE = (
    "- 이 사안은 {main_company}에 **부정적 파급** 방향입니다. 기자·고객·임원이 곧\n"
    "  \"귀사는 괜찮은가\"를 물어올 수 있습니다. 그 질문에 답하기 전에 내부에서 무엇을\n"
    "  확인해야 하는지를 recommendations에 담으세요.\n"
    "- 아직 확인되지 않은 우리 회사의 안전성을 단정하는 문장을 쓰지 마세요."
)

# 사례 출처가 두 갈래가 됐다(검수 DB curated / 웹 검색 web_search - case_search.py).
# 그래서 '전부 미검수'라고 단정하던 원문을 '검수 표시 없는 것만 미검수'로 고쳤다.
_CASE_RULE_ALLOWED = (
    "- 아래 [참고 사례]를 인용할 때는 case_id를 cited_case_ids에 넣으세요.\n"
    "- **주어진 사례 외에 당신이 알고 있는 외부 사례를 언급하지 마세요.**\n"
    "- ※미검수 표시가 붙은 사례는 웹 검색으로 모은 자료입니다. 단정적으로 인용하지\n"
    "  말고 \"보도에 따르면\" 수준으로 쓰세요."
)
_CASE_RULE_FORBIDDEN = (
    "- **과거 사례를 인용하지 마세요.** 검증된 사례가 없습니다. cited_case_ids는\n"
    "  반드시 빈 배열이어야 합니다."
)


# 재생성 지시문. generate.py의 regenerate_with_feedback와 같은 구조다 - 초안을 통째로
# 다시 쓰게 하지 않고 무엇이 틀렸는지 지정한다. 통과한 부분까지 새로 쓰면 고칠 곳 말고
# 다른 데가 바뀌어 두 번째 검증에서 새 위반이 생기기 쉽기 때문이다.
_FEEDBACK_PROMPT = (
    "직전 추천방안이 아래 검증 규칙을 위반했습니다. 해당 부분만 고치고 나머지는 유지하세요.\n"
    "{violations}\n\n"
    "[직전 추천방안]\n"
    "{previous}"
)


def build_system_prompt(peer: dict) -> str:
    """방향에 따라 규칙 블록만 갈아 끼운다. 뼈대는 하나다."""
    direction = peer.get("impact_direction", "")
    main_company = peer.get("main_company_name", "우리 회사")

    if direction == "반사이익":
        direction_rule = _DIRECTION_RULE_UPSIDE.format(main_company=main_company)
    else:
        direction_rule = _DIRECTION_RULE_DOWNSIDE.format(main_company=main_company)

    has_cases = bool(peer.get("cases"))

    return _SYSTEM_PROMPT.format(
        peer_company=peer.get("company_name", "타사"),
        main_company=main_company,
        impact_direction=direction or "미상",
        impact_level=peer.get("impact_level", "미상"),
        reason=peer.get("reason", "(제공되지 않음)"),
        max_recommendations=MAX_RECOMMENDATIONS,
        max_avoid=MAX_AVOID,
        direction_rule=direction_rule,
        case_rule=_CASE_RULE_ALLOWED if has_cases else _CASE_RULE_FORBIDDEN,
    )


# 채널 조건부 법령 참고 (팀 합의 - 검증 규칙이 아니라 프롬프트 참고 자료).
# 동종사 사안이라 법정 기한이 우리에게 발동하지는 않지만, 동일_취약점_보유·규제_조사_확대
# 채널일 때는 우리 쪽 법령이 점검 대상이 된다. 법령 목록은 service가
# retrieval.KoreanRegulationMapper.lookup(유형코드)로 조회해 peer["regulations"]로
# 넣어 준다(verified 조문만 옴 - 지어낸 조문 차단은 그쪽 계층의 몫). 매핑이 없는
# 유형은 블록 자체가 들어가지 않는다.
_REGULATION_CHANNELS = {"동일_취약점_보유", "규제_조사_확대"}

# 데이터 품질 블록에 넣는 누락 필드의 한글 이름. 원래 raw 필드명을 그대로 넣었더니
# 모델이 limitations에 spread_stage 같은 내부 표기를 그대로 받아 적었다(작업본 골든
# 3건 전부). 규칙 6이 그걸 위반으로 잡으므로, 원인인 이쪽을 사람 언어로 고친다.
# 키 목록은 schema.AlertPayload.missing_fields()가 검사하는 6개와 같다.
_FIELD_KO = {
    "industry": "업종",
    "spread_stage": "확산 단계",
    "days_since_last_alert": "직전 경보 이후 경과일",
    "attribution": "판정 기여 지표",
    "daily_series": "일별 언급 추이",
    "negative_ratio": "부정 비율",
}


def build_user_prompt(peer: dict) -> str:
    """[영향 경로] + [관찰 지점] + [참고 사례] + [참고 법령] 순으로 근거를 조립한다.

    영향 경로를 맨 앞에 두는 이유: 권고가 여기에 매달려야 하므로 가장 먼저 읽혀야 한다.
    """
    parts: list[str] = []

    channels = peer.get("impact_channels") or []
    if channels:
        parts.append(
            "[영향 경로 - 권고는 반드시 이 중 하나에 매달 것. channel에 이름을 그대로 쓸 것]\n"
            + "\n".join(f"- {c}" for c in channels)
        )
    else:
        parts.append("[영향 경로]\n- (식별된 경로 없음. 권고를 만들지 말고 관찰만 제안할 것)")

    watch = peer.get("watch_points") or []
    if watch:
        parts.append(
            "[앞 단계가 정한 관찰 지점 - 다시 쓰지 말고, 재경보 기준을 정하는 데 참고할 것]\n"
            + "\n".join(f"- {w}" for w in watch)
        )

    cases = peer.get("cases") or []
    if cases:
        lines = []
        for c in cases:
            tag = " ※웹 검색 수집·미검수" if c.get("provenance") == "web_search" else ""
            lines.append(
                f"- [{c.get('case_id')}] {c.get('title')}{tag}\n"
                f"  무슨 일: {c.get('summary_what', '')}\n"
                f"  교훈: {c.get('lesson', '')}"
            )
        parts.append(
            "[참고 사례 - 인용 시 case_id를 cited_case_ids에 넣을 것]\n" + "\n".join(lines)
        )

    regs = peer.get("regulations") or []
    if regs and set(channels) & _REGULATION_CHANNELS:
        parts.append(
            "[참고 법령 - 우리 쪽 점검 항목 후보]\n"
            + "\n".join(
                f"- {r.get('law_name', '')} {r.get('article', '')} - {r.get('requirement', '')}"
                for r in regs
            )
            + "\n- 위 목록에 있는 것만 인용하고, 목록 밖의 조문을 기억으로 지어내지 말 것"
        )

    missing = peer.get("missing_input_fields") or []
    quality = []
    if missing:
        quality.append(
            "판정에 쓰이지 못한 입력 항목: "
            + ", ".join(_FIELD_KO.get(m, m) for m in missing)
        )
    if peer.get("needs_review"):
        quality.append("영향 판정 확신도가 낮아 사람 검토가 필요한 건")
    conf = peer.get("confidence")
    if conf is not None and conf < 0.7:
        quality.append(f"영향 판정 확신도 {conf}로 낮음 - 단정적 표현을 피할 것")
    if quality:
        parts.append("[데이터 품질 - limitations에 반영할 것]\n" + "\n".join(f"- {q}" for q in quality))

    return "\n\n".join(parts)


def recommend(peer: dict) -> tuple[dict, dict[str, int]]:
    """추천방안 1개를 만든다. 반환: (추천방안, 토큰 사용량).

    peer는 service._build_peer_content가 impact.analyze 결과에 사례·법령을 합쳐 만든
    dict다. 1회 생성이다 - 동종 기업 권고는 감사 대상 문서가 아니고, 다중 생성이 필요할
    만큼 결론이 갈리는 판단도 아니다(메인 경로의 스탠스 다중 생성과 대비되는 지점).
    """
    return structured_call(
        system=build_system_prompt(peer),
        user=build_user_prompt(peer),
        schema=RECOMMEND_SCHEMA["schema"],
        schema_name=RECOMMEND_SCHEMA["name"],
    )


def regenerate_with_feedback(
    peer: dict,
    previous: dict,
    violations: list[str],
) -> tuple[dict, dict[str, int]]:
    """자동 검증 실패 시 위반 항목을 지정해 재생성한다 (generate.regenerate_with_feedback와 짝).

    검증된 패턴의 재사용이자, 보고서·추천 두 경로의 실패 처리 동작을 맞추는 장치다.
    같은 시스템·유저 프롬프트를 다시 쓰고, 위반 목록과 직전 추천방안을 user 뒤에
    이어 붙인다(Responses API는 메시지 배열이 없어 generate.py와 같은 방식으로 합친다).
    """
    feedback = _FEEDBACK_PROMPT.format(
        violations="\n".join(f"- {v}" for v in violations),
        previous=json.dumps(previous, ensure_ascii=False),
    )
    return structured_call(
        system=build_system_prompt(peer),
        user=build_user_prompt(peer) + "\n\n" + feedback,
        schema=RECOMMEND_SCHEMA["schema"],
        schema_name=RECOMMEND_SCHEMA["name"],
    )


# ---------------------------------------------------------------- 자동 검증

# 규칙 5가 잡는 '우리 사고 아님' 해명 패턴. 사고/사건/유출 + (조사 가/는/은/이) +
# 아니- 활용형(아니/아님/아닌/아닙)만 본다. 작업본의 초기 프롬프트 테스트에서 headline이
# "11번가 자체 사고 아님: ..."으로 시작해 행동 요약이 밀려났고, 프롬프트 수정으로 해소했지만
# 프롬프트 준수는 확률적이라 코드로 잠근다(회귀 가드). '무관'은 넣지 않는다 -
# "무관 여부를 확인한다"는 정당한 행동 문장이다.
_RULE5_HEADLINE_DISCLAIMER = re.compile(
    r"(?:사고|사건|유출)\s*(?:[가는은이]\s*)?아[니님닌닙]"
)

# 규칙 6이 잡는 내부 표기 (팀 합의: 이 경로의 독자는 주가·마케팅 담당자라 메인보다
# 위험이 크다). 목록 유지 대신 패턴으로 잡는다 - 언더스코어 조어는 배선용 enum이든
# (규제_조사_확대) 내부 필드명이든(negative_ratio) 산문에 나올 일 자체가 없어서다.
# channel·timeframe 같은 구조 필드는 검사하지 않는다 - 거기는 enum이 있어야 정상이고
# (규칙 1이 요구), 사람 언어 변환은 렌더러의 몫이다.
_RULE6_SNAKE_KO = re.compile(r"[가-힣0-9]+(?:_[가-힣0-9]+)+")
_RULE6_SNAKE_EN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_RULE6_URL = re.compile(r"https?://")
_RULE6_PATTERNS = (
    (_RULE6_SNAKE_KO, "내부 표기"),
    (_RULE6_SNAKE_EN, "내부 필드명"),
    (_RULE6_URL, "생 URL"),
)


def verify_recommendation(rec: dict, peer: dict) -> list[str]:
    """추천방안을 담당자에게 올리기 전에 코드가 먼저 검사한다.

    메인 경로의 verify.py(규칙 레지스트리 기준 10개)보다 적은 6개다. 체크리스트 기한·
    대응 주체가 없는 산출물이라 해당 규칙이 성립하지 않고, 법령은 검증 대신 프롬프트
    참고 주입으로 대체했다(build_user_prompt의 [참고 법령] - 팀 합의 반영).
    규칙 5·6은 headline과 본문이 사람 언어여야 한다는 프롬프트 계약을 코드로 굳힌
    회귀 가드고, 규칙 3에는 '수집한 사례를 하나도 안 쓴 경우' 검출이 포함된다.

    반환: 위반 사유 목록. 빈 리스트면 통과.
    """
    violations: list[str] = []

    # 규칙 1 - 권고가 앞 단계가 식별한 영향 경로에 매달려 있는가.
    #   메인 경로에서 인용 사례를 검색 결과로 제한한 것과 같은 장치다. 경로 밖의 권고는
    #   근거 없이 지어낸 것이다.
    channels = set(peer.get("impact_channels") or [])
    for r in rec.get("recommendations", []):
        ch = r.get("channel")
        if ch not in channels:
            violations.append(f"영향 경로에 없는 채널에 권고: {ch}")

    # 규칙 2 - 인용한 사례가 실제로 주어진 것인가(환각 차단).
    available = {c.get("case_id") for c in (peer.get("cases") or [])}
    for cid in rec.get("cited_case_ids", []):
        if cid not in available:
            violations.append(f"존재하지 않는 사례 인용: {cid}")

    # 규칙 3 - 개수 상한. strict 스키마가 maxItems를 지원하지 않아 코드로 검사한다.
    n_rec = len(rec.get("recommendations", []))
    if n_rec > MAX_RECOMMENDATIONS:
        violations.append(f"권고 {n_rec}개 (최대 {MAX_RECOMMENDATIONS}개)")
    n_avoid = len(rec.get("avoid", []))
    if n_avoid > MAX_AVOID:
        violations.append(f"금지 항목 {n_avoid}개 (최대 {MAX_AVOID}개)")
    #   확장(팀 합의) - 규칙 2는 '인용한 게 실재하는가'만 본다. 사례를 수집해 줬는데
    #   하나도 안 쓰면 검색 비용(LLM 1회+토큰)이 결과물에 통째로 버려지는데 아무도 못
    #   잡았다. case_insights 필드가 없는 경량 형식이라 인용 여부만 본다.
    n_cases = len(peer.get("cases") or [])
    if n_cases and not rec.get("cited_case_ids"):
        violations.append(f"사례 {n_cases}건이 주어졌는데 인용 0건 (수집 비용이 결과물에 반영되지 않음)")

    # 규칙 4 - 반사이익인데 경고가 없는가.
    #   경쟁사 악재를 활용하는 문장이 외부로 새면 그 자체가 리스크다. 방향이 반사이익일
    #   때 avoid가 비어 있으면 그 경고를 빠뜨린 것이다.
    if peer.get("impact_direction") == "반사이익" and not rec.get("avoid"):
        violations.append("반사이익 사안인데 금지 항목이 비어 있음")

    # 규칙 5 - headline이 '우리 사고 아님' 해명을 반복하는가.
    #   그 해소는 source_event의 역할이다(프롬프트 명시). headline만 검사한다 -
    #   source_event에는 이 해명이 있어야 정상이라, 범위를 넓히면 정상 출력이 전부 걸린다.
    m = _RULE5_HEADLINE_DISCLAIMER.search(rec.get("headline") or "")
    if m:
        violations.append(
            f"headline에 '우리 사고 아님' 해명 포함: '{m.group(0)}' "
            "(해명은 source_event 몫, headline은 행동 요약만)"
        )

    # 규칙 6 - 산문 필드에 내부 표기·필드명·생 URL이 새어 나왔는가.
    #   프롬프트 [읽는 사람] 규칙을 코드로 강제한다(메인 경로 규칙 10과 짝). 작업본에서
    #   골든 3건 전부의 limitations에 spread_stage가 노출된 채 검증을 통과한 실례가 근거다.
    prose: list[tuple[str, str]] = [
        ("headline", rec.get("headline") or ""),
        ("source_event", rec.get("source_event") or ""),
        ("realert_condition", rec.get("realert_condition") or ""),
        ("limitations", rec.get("limitations") or ""),
    ]
    for i, r in enumerate(rec.get("recommendations", []) or [], 1):
        for key in ("action", "rationale", "owner_hint"):
            prose.append((f"recommendations[{i}].{key}", str(r.get(key) or "")))
    for i, a in enumerate(rec.get("avoid", []) or [], 1):
        prose.append((f"avoid[{i}]", a if isinstance(a, str) else ""))
    for field_name, text in prose:
        for pattern, label in _RULE6_PATTERNS:
            hit = pattern.search(text)
            if hit:
                violations.append(
                    f"{field_name}에 {label} 노출: '{hit.group(0)}' "
                    "(본문은 사람 언어로 - 출처·표기는 별도 영역의 몫)"
                )

    return violations
