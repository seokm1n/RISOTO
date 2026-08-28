"""동종 기업 추천방안 출력 JSON 스키마 + 상수.

메인 경로의 report_schema.py와 짝을 이루지만 **훨씬 가볍다**. 동종 기업 추천은 전략·
체크리스트·법령까지 갖춘 보고서가 아니라 "그래서 뭘 하라"만 담는다(원 인수인계의 '심플하게').

**앞 단계가 이미 채워둔 것은 다시 만들지 않는다.** impact.py가 무슨 일인지·어느 방향인지·
노출 수준·영향 경로·관찰 지점을 이미 판정했다. 이 단계가 더하는 것은 네 가지뿐이다.
  1) 행동      - 그래서 무엇을 하나
  2) 선확인    - 대외 답변 전에 내부에서 확인해야 할 것
  3) 금지      - 하지 말아야 할 것 (특히 반사이익일 때)
  4) 재경보    - 어느 선을 넘으면 다시 알릴 것인가

**권고는 반드시 impact_channels 중 하나에 매달린다.** 메인 경로에서 인용 사례를 검색
결과로 제한한 것과 같은 장치다. 앞 단계가 식별하지 않은 경로에 대한 권고는 근거 없는
추측이므로 검증 규칙 1이 걸러낸다.

OpenAI strict 모드는 개수 제한(maxItems)을 지원하지 않으므로 상한은 코드로 검사한다
(verify_recommendation 규칙 3). report_schema.py와 같은 이유다.

각 필드가 검증에 어떻게 쓰이는지:
  recommendations[].channel  -> 규칙 1 (앞 단계가 식별한 영향 경로인가)
  cited_case_ids             -> 규칙 2 (없는 사례 인용 차단) · 규칙 3 확장 (수집 사례 미인용 검출)
  recommendations 개수       -> 규칙 3 (상한)
  avoid                      -> 규칙 4 (반사이익일 때 최소 1개)
  headline                   -> 규칙 5 ('우리 사고 아님' 해명 금지 - 해명은 source_event 몫)
  산문 필드 전반              -> 규칙 6 (내부 표기·필드명·생 URL 노출 금지 - 메인 규칙 10과 짝)
"""
from __future__ import annotations

# 방향값의 원천은 impact.py다 - 같은 튜플을 두 곳에 두면 어긋날 수 있어 가져와 쓴다.
from .impact import IMPACT_DIRECTIONS  # noqa: F401  (re-export: 소비자는 이 모듈에서 찾는다)

# 권고 실행 시점. 메인 경로의 deadline_hours(72시간 이내)와 달리 동종 기업 대응은
# 긴급하지 않다. 시간 단위가 아니라 구간 enum으로 두는 이유다.
TIMEFRAMES = ("즉시", "1주_내", "2주_내", "1개월_내")

# 심플하게가 요구사항이라 상한을 낮게 잡는다. 담당자가 실제로 실행할 수 있는 양이
# 리포트의 가치를 정한다 - 20건을 받으면 사람 일을 덜어준 게 아니라 새로 만든 것이다.
MAX_RECOMMENDATIONS = 4
MAX_AVOID = 3

RECOMMEND_SCHEMA = {
    "name": "peer_recommendation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            # 동종 기업에서 무슨 일이 있었는지 1~2문장. 새 판단이 아니라 앞 단계 reason을
            # 담당자 언어로 줄인 것이다. **"귀사에서 터진 사건이 아니다"를 여기서 해소한다**
            # - 경보만 보고 자기 회사 사고로 오해하는 30분을 없앤다.
            "source_event": {"type": "string"},
            # recommendations를 한 문장으로 압축한 행동 요약. source_event가 '상황'이면
            # 이쪽은 '행동'이다. 둘 다 상황을 말하면 담당자가 같은 말을 두 번 읽는다.
            "headline": {"type": "string"},
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        # 앞 단계 impact_channels 중 하나. 규칙 1이 대조한다.
                        "channel": {"type": "string"},
                        "action": {"type": "string"},
                        "rationale": {"type": "string"},
                        # 참이면 대외 답변 전에 내부 확인이 선행되어야 하는 항목이다.
                        # 담당자가 확인 없이 답하는 것이 이 상황의 대표적 사고 경로다.
                        "verify_first": {"type": "boolean"},
                        "owner_hint": {"type": "string"},
                        "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                    },
                    "required": [
                        "channel",
                        "action",
                        "rationale",
                        "verify_first",
                        "owner_hint",
                        "timeframe",
                    ],
                    "additionalProperties": False,
                },
            },
            # 하지 말아야 할 것. 금지문이 생성 제어에 잘 먹히는 것과 같은 이유로,
            # 담당자에게도 "무엇을 하라"보다 "무엇을 하지 마라"가 사고를 더 잘 막는다.
            "avoid": {"type": "array", "items": {"type": "string"}},
            # 앞 단계 watch_points가 '무엇을 볼 것인가'라면 이것은 '어느 선을 넘으면
            # 다시 알릴 것인가'다. 관찰만 하고 기준이 없으면 아무도 다시 보지 않는다.
            "realert_condition": {"type": "string"},
            "limitations": {"type": "string"},
            "cited_case_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "source_event",
            "headline",
            "recommendations",
            "avoid",
            "realert_condition",
            "limitations",
            "cited_case_ids",
        ],
        "additionalProperties": False,
    },
}
