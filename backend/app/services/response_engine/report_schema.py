"""보고서 출력 JSON 스키마 + 전략/책임 enum.

OpenAI strict 모드는 개수 제한(maxItems/minItems)을 지원하지 않는다. 그래서 전략 3개 이하,
주 리스크 2개 이하 같은 상한은 스키마가 아니라 **코드로 검사한다**(verify.py 규칙 4).
문서 §6의 자동 검증 4번이 존재하는 이유가 이것이다.

각 필드가 검증에 어떻게 쓰이는지:
  strategies[].strategy_type       -> 규칙 3 (책임 서사와 전략 상충)
  strategies[].target_stakeholder  -> 규칙 6 (전략 대상이 유형의 담당 주체와 일치)
  checklist[].deadline_hours       -> 규칙 5 (72시간 범위)
  cited_mention_ids                -> 규칙 2 (선별 목록에 없는 원문 인용 차단)
  cited_case_ids                   -> 규칙 1 (없는 사례 인용 차단)
"""
from __future__ import annotations

from .risk_types import Stakeholder

RESPONSIBILITY_VALUES = ("피해자", "사고", "예방가능")

STRATEGY_TYPES = (
    "사실관계_정정",
    "사과_시정",
    "보상",
    "재발방지",
    "소통강화",
    "법적대응",
    "모니터링_유지",
    "부인_반박",
)

# 책임이 '예방가능'인데 이 전략을 쓰면 여론 역풍을 부르는 조합이다(자동 검증 3번).
FORBIDDEN_WHEN_PREVENTABLE = ("부인_반박",)

MAX_STRATEGIES = 3
MAX_PRIMARY_RISKS = 2
MAX_CHECKLIST_HOURS = 72
MIN_CHECKLIST_HOURS = 1

REPORT_SCHEMA = {
    "name": "crisis_response_report",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "scenario_stance": {"type": "string"},
            # 탭에 걸리는 짧은 이름. stance 코드(선제_공개 등)는 내부 용어라 담당자가
            # 무엇이 다른지 알 수 없다. 사람이 읽을 이름을 모델이 직접 짓게 한다.
            "scenario_headline": {"type": "string"},
            "scenario_tradeoff": {"type": "string"},
            "summary_points": {"type": "array", "items": {"type": "string"}},
            "judgment_basis": {"type": "string"},
            "case_insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "case_title": {"type": "string"},
                        "insight": {"type": "string"},
                    },
                    "required": ["case_id", "case_title", "insight"],
                    "additionalProperties": False,
                },
            },
            "risk_assessment": {
                "type": "object",
                "properties": {
                    "responsibility": {"type": "string", "enum": list(RESPONSIBILITY_VALUES)},
                    "primary_risks": {"type": "array", "items": {"type": "string"}},
                    "secondary_risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["responsibility", "primary_risks", "secondary_risks"],
                "additionalProperties": False,
            },
            "strategies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                        "strategy_type": {"type": "string", "enum": list(STRATEGY_TYPES)},
                        "target_stakeholder": {
                            "type": "string",
                            "enum": [s.value for s in Stakeholder],
                        },
                    },
                    "required": ["title", "detail", "strategy_type", "target_stakeholder"],
                    "additionalProperties": False,
                },
            },
            "checklist": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "owner": {"type": "string"},
                        "deadline_hours": {"type": "integer"},
                    },
                    "required": ["task", "owner", "deadline_hours"],
                    "additionalProperties": False,
                },
            },
            "monitoring_metrics": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "string"},
            "cited_mention_ids": {"type": "array", "items": {"type": "string"}},
            "cited_case_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "scenario_stance",
            "scenario_headline",
            "scenario_tradeoff",
            "summary_points",
            "judgment_basis",
            "case_insights",
            "risk_assessment",
            "strategies",
            "checklist",
            "monitoring_metrics",
            "limitations",
            "cited_mention_ids",
            "cited_case_ids",
        ],
        "additionalProperties": False,
    },
}
