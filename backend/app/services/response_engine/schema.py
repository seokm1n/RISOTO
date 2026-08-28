"""상단 위기판정 모델 -> 대응방안 생성 사이의 입력 페이로드, 그리고 원문/기여지표 구조.

**필드명 변경에 관대하게 설계한 이유**: 상단 LightGBM 파이프라인이 아직 확정 전이라
넘어오는 키 이름이 바뀔 수 있다. 그래서 각 필드마다 별칭(alias) 목록을 두고 먼저 맞는
것을 쓴다. 이름이 바뀌면 _ALIASES에 한 줄만 추가하면 되고 파이프라인 코드는 손대지 않는다.

없는 필드는 None으로 두고 죽지 않는다 - 워크플로우 문서 v5 §9에 정리된 대로 spread_stage,
industry, 이력 4종은 아직 산출 로직/컬럼 자체가 없어서 실제로 안 들어온다. 이 값이 비었을 때
무엇을 포기하는지는 각 소비 지점(tier.py의 확산단계 상향, generate.py의 맥락 블록)에서
개별적으로 처리한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 내부 표준 이름 -> 들어올 수 있는 키 후보들. 앞에서부터 먼저 발견되는 것을 쓴다.
_ALIASES: dict[str, tuple[str, ...]] = {
    "alert_id": ("alert_id", "id", "event_id"),
    "company_id": ("company_id", "companyId"),
    "company_name": ("company_name", "company", "companyName", "brand"),
    "industry": ("industry", "sector"),
    "main_services": ("main_services", "services"),
    "company_role": ("company_role", "role", "monitor_role"),
    "main_company_name": ("main_company_name", "my_company", "user_company"),
    "main_company_industry": ("main_company_industry", "my_company_industry"),
    "main_company_services": ("main_company_services", "my_company_services"),
    "window_start": ("window_start", "start"),
    "window_end": ("window_end", "end"),
    "snapshot_at": ("snapshot_at", "created_at", "detected_at"),
    "crisis_probability": ("crisis_probability", "risk_score", "probability", "score"),
    "model_version": ("model_version", "risk_model_version", "version"),
    "escalation_tier": ("escalation_tier", "tier"),
    "attribution": ("attribution", "shap_top_features", "feature_contributions"),
    "mention_count": ("mention_count", "n_mentions", "daily_count"),
    "baseline_mean": ("baseline_mean", "mention_baseline", "rolling_mean"),
    "negative_ratio": ("negative_ratio", "neg_ratio"),
    "negative_ratio_baseline": ("negative_ratio_baseline", "neg_ratio_baseline"),
    "source_mix": ("source_mix", "sources"),
    "n_unique_channels": ("n_unique_channels", "distinct_authors", "unique_channels"),
    "spread_stage": ("spread_stage", "stage"),
    "daily_series": ("daily_series", "series", "timeseries"),
    "mentions": ("mentions", "evidence", "snippets", "raw_payload"),
    "n_mentions_7d": ("n_mentions_7d", "n_comments_7d"),
    "baseline_days": ("baseline_days",),
    "baseline_window_days": ("baseline_window_days", "window_days"),
    "is_warmup": ("is_warmup", "warmup"),
    "days_since_last_alert": ("days_since_last_alert",),
    "last_risk_type": ("last_risk_type",),
    "last_report_id": ("last_report_id",),
}


def _pick(raw: dict[str, Any], key: str) -> Any:
    for alias in _ALIASES.get(key, (key,)):
        if alias in raw and raw[alias] is not None:
            return raw[alias]
    return None


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_str(v: Any) -> str | None:
    return str(v) if v is not None else None


@dataclass
class Attribution:
    """위기 판정 모델이 내놓은 기여 지표 하나. 문서 §5의 정량 근거가 이걸로 만들어진다."""

    feature: str
    value: float | None = None
    baseline: float | None = None
    percentile: float | None = None
    direction: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Attribution":
        return cls(
            feature=str(raw.get("feature") or raw.get("name") or "unknown"),
            value=_as_float(raw.get("value")),
            baseline=_as_float(raw.get("baseline")),
            percentile=_as_float(raw.get("percentile")),
            direction=_as_str(raw.get("direction")),
        )


@dataclass
class Mention:
    """선별된 원문 1건. mention_id는 자동 검증 2번(인용 대조)의 키라 반드시 있어야 한다."""

    mention_id: str
    text: str
    source: str | None = None
    url: str | None = None
    published_at: str | None = None
    like_count: int | None = None
    reply_count: int | None = None
    sentiment: str | None = None
    pick_reason: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Mention":
        mid = raw.get("mention_id") or raw.get("id") or raw.get("url")
        body = raw.get("text") or raw.get("body") or ""
        return cls(
            mention_id=str(mid),
            text=str(body),
            source=_as_str(raw.get("source")),
            url=_as_str(raw.get("url")),
            published_at=_as_str(raw.get("published_at")),
            like_count=_as_int(raw.get("like_count")),
            reply_count=_as_int(raw.get("reply_count")),
            sentiment=_as_str(raw.get("sentiment")),
            pick_reason=_as_str(raw.get("pick_reason")),
        )


@dataclass
class AlertPayload:
    """위기 판정 게이트를 통과한 1건. 이 워크플로우의 유일한 입력이다."""

    company_name: str
    alert_id: str | None = None
    company_id: str | None = None
    industry: str | None = None
    main_services: str | None = None
    # 이 알림의 대상 기업이 사용자에게 메인인지 동종인지. company.py 참고 - 역할은 기업이
    # 아니라 (사용자, 기업) 관계의 속성이라 페이로드로 실어 온다.
    company_role: str | None = None
    # 동종 기업 알림일 때 "우리 기업"이 누구인지. 영향 분석 프롬프트에 들어간다.
    main_company_name: str | None = None
    main_company_industry: str | None = None
    main_company_services: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    snapshot_at: str | None = None

    crisis_probability: float | None = None
    model_version: str | None = None
    escalation_tier: str | None = None
    attribution: list[Attribution] = field(default_factory=list)

    mention_count: int | None = None
    baseline_mean: float | None = None
    negative_ratio: float | None = None
    negative_ratio_baseline: float | None = None
    # '평소'가 며칠 기준인지. build_core_features.py가 WINDOW=7로 직전 7일 이동평균을
    # 쓰므로 기본값을 7로 둔다. 보고서에 "평소"라고만 쓰면 독자가 어느 기간과 비교한
    # 숫자인지 알 수 없어 해석이 불가능하므로, 이 값을 프롬프트에 넣어 기간을 밝히게 한다.
    baseline_window_days: int = 7
    source_mix: dict[str, float] = field(default_factory=dict)
    n_unique_channels: int | None = None
    spread_stage: str | None = None
    daily_series: list[dict[str, Any]] = field(default_factory=list)

    mentions: list[Mention] = field(default_factory=list)

    n_mentions_7d: int | None = None
    baseline_days: int | None = None
    is_warmup: bool | None = None

    days_since_last_alert: int | None = None
    last_risk_type: str | None = None
    last_report_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AlertPayload":
        company = _pick(raw, "company_name")
        if not company:
            raise ValueError("company_name(또는 company/brand) 필드는 반드시 필요합니다.")

        source_mix = _pick(raw, "source_mix") or {}
        return cls(
            company_name=str(company),
            alert_id=_as_str(_pick(raw, "alert_id")),
            company_id=_as_str(_pick(raw, "company_id")),
            industry=_as_str(_pick(raw, "industry")),
            main_services=_as_str(_pick(raw, "main_services")),
            company_role=_as_str(_pick(raw, "company_role")),
            main_company_name=_as_str(_pick(raw, "main_company_name")),
            main_company_industry=_as_str(_pick(raw, "main_company_industry")),
            main_company_services=_as_str(_pick(raw, "main_company_services")),
            window_start=_as_str(_pick(raw, "window_start")),
            window_end=_as_str(_pick(raw, "window_end")),
            snapshot_at=_as_str(_pick(raw, "snapshot_at")),
            crisis_probability=_as_float(_pick(raw, "crisis_probability")),
            model_version=_as_str(_pick(raw, "model_version")),
            escalation_tier=_as_str(_pick(raw, "escalation_tier")),
            attribution=[Attribution.from_dict(a) for a in (_pick(raw, "attribution") or []) if isinstance(a, dict)],
            mention_count=_as_int(_pick(raw, "mention_count")),
            baseline_mean=_as_float(_pick(raw, "baseline_mean")),
            negative_ratio=_as_float(_pick(raw, "negative_ratio")),
            negative_ratio_baseline=_as_float(_pick(raw, "negative_ratio_baseline")),
            baseline_window_days=_as_int(_pick(raw, "baseline_window_days")) or 7,
            source_mix={str(k): _as_float(v) or 0.0 for k, v in source_mix.items()},
            n_unique_channels=_as_int(_pick(raw, "n_unique_channels")),
            spread_stage=_as_str(_pick(raw, "spread_stage")),
            daily_series=list(_pick(raw, "daily_series") or []),
            mentions=[Mention.from_dict(m) for m in (_pick(raw, "mentions") or []) if isinstance(m, dict)],
            n_mentions_7d=_as_int(_pick(raw, "n_mentions_7d")),
            baseline_days=_as_int(_pick(raw, "baseline_days")),
            is_warmup=_pick(raw, "is_warmup"),
            days_since_last_alert=_as_int(_pick(raw, "days_since_last_alert")),
            last_risk_type=_as_str(_pick(raw, "last_risk_type")),
            last_report_id=_as_str(_pick(raw, "last_report_id")),
        )

    def missing_fields(self) -> list[str]:
        """아직 안 들어온 필드 목록. 파이프라인이 무엇을 포기했는지 로그로 남기는 용도."""
        checks = {
            "industry": self.industry,
            "spread_stage": self.spread_stage,
            "days_since_last_alert": self.days_since_last_alert,
            "attribution": self.attribution or None,
            "daily_series": self.daily_series or None,
            "negative_ratio": self.negative_ratio,
        }
        return [k for k, v in checks.items() if v is None]
