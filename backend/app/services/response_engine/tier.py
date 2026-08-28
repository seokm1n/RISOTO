"""2단계 대응 등급 산출 (문서 §4).

등급은 3단이다. 알림 채널이 셋이라 3단이며, 등급 수가 채널 수보다 많으면 되레 실제로
달라지는 게 없다는 문서의 판단을 그대로 따른다.

**유형 가중치를 위기 확률 안에 섞지 않는 이유**: 같은 신호를 두 번 반영하지 않기 위해서고,
정책이 바뀌면 매트릭스 해당 칸만 고치면 되기 때문이다. 위기 확률은 상단 모델이 준 값
그대로 두고, 유형 민감도는 여기서만 곱한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .risk_types import Sensitivity, get

# 확률 구간 경계. 문서 §11에 적힌 대로 판정 모델 성능 측정 후 재설정해야 하는 잠정값이다.
PROB_HIGH = 0.8
PROB_MID = 0.6

TIER_ORDER: tuple[str, ...] = ("T1_관찰", "T2_주시", "T3_긴급")

TIER_POLICY: dict[str, dict[str, str]] = {
    "T1_관찰": {"알림": "대시보드", "검토": "비업무 중", "재생성": "1회"},
    "T2_주시": {"알림": "담당자 알림", "검토": "24시간 내", "재생성": "1회"},
    "T3_긴급": {"알림": "즉시 알림", "검토": "2인 검토 + 법무 검토 권장", "재생성": "3회 후 합의"},
}

# [확률 구간][유형 민감도] -> 등급. 문서 §4의 표를 그대로 옮긴 것이다.
_MATRIX: dict[str, dict[Sensitivity, str]] = {
    "상": {Sensitivity.HIGH: "T3_긴급", Sensitivity.MEDIUM: "T3_긴급", Sensitivity.LOW: "T2_주시"},
    "중": {Sensitivity.HIGH: "T3_긴급", Sensitivity.MEDIUM: "T2_주시", Sensitivity.LOW: "T2_주시"},
    "하": {Sensitivity.HIGH: "T2_주시", Sensitivity.MEDIUM: "T1_관찰", Sensitivity.LOW: "T1_관찰"},
}

# 확산 단계가 이 값이면 한 단계 상향(문서 §4).
_ESCALATING_STAGE = "언론보도"


@dataclass
class TierDecision:
    tier: str
    prob_band: str
    sensitivity: str
    spread_bumped: bool
    notes: list[str]

    @property
    def policy(self) -> dict[str, str]:
        return TIER_POLICY[self.tier]


def _prob_band(p: float | None) -> tuple[str, list[str]]:
    if p is None:
        # 상단 모델 확률이 없으면 게이트를 통과했다는 사실만 아는 셈이라, 중간 구간으로
        # 보수적으로 잡는다. 낮게 잡아 T1로 떨어뜨리면 놓치는 비용이 더 크다.
        return "중", ["crisis_probability가 없어 '중' 구간으로 보수적 처리"]
    if p >= PROB_HIGH:
        return "상", []
    if p >= PROB_MID:
        return "중", []
    return "하", []


def decide(risk_type_code: str, payload) -> TierDecision:
    notes: list[str] = []
    band, band_notes = _prob_band(payload.crisis_probability)
    notes.extend(band_notes)

    sensitivity = get(risk_type_code).sensitivity
    tier = _MATRIX[band][sensitivity]

    bumped = False
    if payload.spread_stage == _ESCALATING_STAGE:
        idx = TIER_ORDER.index(tier)
        if idx < len(TIER_ORDER) - 1:
            tier = TIER_ORDER[idx + 1]
            bumped = True
            notes.append("확산 단계가 언론보도라 한 단계 상향")
    elif payload.spread_stage is None:
        # 문서 §9: spread_stage 산출 로직이 아직 없어 이 규칙이 지금은 작동하지 않는다.
        # 조용히 넘어가면 "규칙이 있는데 왜 안 올랐지"를 나중에 추적 못 하므로 남긴다.
        notes.append("spread_stage 미제공 - 언론보도 상향 규칙 미적용")

    return TierDecision(
        tier=tier,
        prob_band=band,
        sensitivity=sensitivity.value,
        spread_bumped=bumped,
        notes=notes,
    )
