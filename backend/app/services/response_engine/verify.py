"""5단계 자동 검증 (문서 §6의 7개 규칙).

**규칙을 레지스트리로 만든 이유**: 7개 중 일부는 아직 자산(사례 DB, 법령 매핑표)이 없어
검사할 수가 없다. 이걸 코드에서 지워버리면 나중에 자산이 생겼을 때 누가 다시 넣어야 하는지
잊힌다. 그래서 규칙은 전부 살려 두고, 각 규칙이 **자기가 지금 검사 가능한지(is_active)**를
스스로 판단하게 했다. 자산이 없으면 FAIL이 아니라 SKIP으로 결과에 남고, 자산이 채워지면
코드 수정 없이 자동으로 켜진다.

현재 상태:
  1 사례 인용   ACTIVE  (사례 DB가 비면 no_case_mode라 '인용 0건인지'를 검사)
  2 원문 인용   ACTIVE
  3 책임-전략   ACTIVE
  4 개수 상한   ACTIVE  (strict 스키마가 maxItems를 못 걸어 코드로 검사)
  5 기한 범위   ACTIVE
  6 전략 대상   ACTIVE
  7 법령 기한   SKIP    (법령 매핑표가 없음. 매핑표가 채워지면 자동 활성)
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Callable

from .evidence import Evidence
from .report_schema import (
    FORBIDDEN_WHEN_PREVENTABLE,
    MAX_CHECKLIST_HOURS,
    MAX_PRIMARY_RISKS,
    MAX_STRATEGIES,
    MIN_CHECKLIST_HOURS,
)
from .risk_types import get


@dataclass
class RuleResult:
    rule_id: int
    name: str
    status: str  # "pass" | "fail" | "skip"
    message: str = ""


@dataclass
class VerifyResult:
    results: list[RuleResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(r.status == "fail" for r in self.results)

    @property
    def violations(self) -> list[str]:
        """재생성 프롬프트에 그대로 넘길 위반 설명 목록."""
        return [f"[규칙 {r.rule_id} {r.name}] {r.message}" for r in self.results if r.status == "fail"]

    @property
    def skipped(self) -> list[str]:
        return [f"규칙 {r.rule_id} {r.name}: {r.message}" for r in self.results if r.status == "skip"]

    def summary(self) -> str:
        n_pass = sum(1 for r in self.results if r.status == "pass")
        n_fail = sum(1 for r in self.results if r.status == "fail")
        n_skip = sum(1 for r in self.results if r.status == "skip")
        return f"통과 {n_pass} / 실패 {n_fail} / 미검사 {n_skip}"


@dataclass
class _Context:
    report: dict[str, Any]
    evidence: Evidence
    risk_type_code: str


# 각 규칙은 (rule_id, 이름, 활성 판정, 검사 함수)로 구성한다.
# 검사 함수는 (통과 여부, 메시지)를 돌려준다.
def _rule1_cases(ctx: _Context) -> tuple[bool, str]:
    cited = set(ctx.report.get("cited_case_ids") or [])
    if ctx.evidence.no_case_mode:
        if cited:
            return False, f"사례 DB가 비어 인용이 금지된 상태인데 사례 {len(cited)}건을 인용했습니다: {sorted(cited)}"
        return True, "no_case_mode - 사례 인용 0건 확인"
    unknown = cited - ctx.evidence.allowed_case_ids
    if unknown:
        return False, f"수집 결과에 없는 사례를 인용했습니다: {sorted(unknown)}"
    return True, f"인용 사례 {len(cited)}건 모두 수집 결과 내"


def _rule2_mentions(ctx: _Context) -> tuple[bool, str]:
    cited = set(ctx.report.get("cited_mention_ids") or [])
    unknown = cited - ctx.evidence.allowed_mention_ids
    if unknown:
        return False, f"선별 목록에 없는 원문을 인용했습니다: {sorted(unknown)}"
    return True, f"인용 원문 {len(cited)}건 모두 선별 목록 내"


def _rule3_responsibility(ctx: _Context) -> tuple[bool, str]:
    assessment = ctx.report.get("risk_assessment") or {}
    responsibility = assessment.get("responsibility")
    if responsibility != "예방가능":
        return True, f"책임 서사가 '{responsibility}'라 상충 검사 대상 아님"
    bad = [
        s.get("title", "?")
        for s in ctx.report.get("strategies") or []
        if s.get("strategy_type") in FORBIDDEN_WHEN_PREVENTABLE
    ]
    if bad:
        return False, f"책임을 '예방가능'으로 보면서 부인·반박 전략을 제시했습니다: {bad}"
    return True, "예방가능 판단과 전략이 상충하지 않음"


def _rule4_counts(ctx: _Context) -> tuple[bool, str]:
    problems = []
    strategies = ctx.report.get("strategies") or []
    if len(strategies) > MAX_STRATEGIES:
        problems.append(f"전략 {len(strategies)}개(상한 {MAX_STRATEGIES})")

    assessment = ctx.report.get("risk_assessment") or {}
    primary = assessment.get("primary_risks") or []
    secondary = assessment.get("secondary_risks") or []
    if len(primary) > MAX_PRIMARY_RISKS:
        problems.append(f"주 리스크 {len(primary)}개(상한 {MAX_PRIMARY_RISKS})")

    overlap = {p.strip() for p in primary} & {s.strip() for s in secondary}
    if overlap:
        problems.append(f"주/부 리스크 중복: {sorted(overlap)}")

    if problems:
        return False, "; ".join(problems)
    return True, f"전략 {len(strategies)}개 / 주 리스크 {len(primary)}개, 상한 이내"


def _rule5_deadline(ctx: _Context) -> tuple[bool, str]:
    bad = []
    for item in ctx.report.get("checklist") or []:
        hours = item.get("deadline_hours")
        if not isinstance(hours, int) or not (MIN_CHECKLIST_HOURS <= hours <= MAX_CHECKLIST_HOURS):
            bad.append(f"{item.get('task', '?')}({hours}시간)")
    if bad:
        return False, f"체크리스트 기한이 {MIN_CHECKLIST_HOURS}~{MAX_CHECKLIST_HOURS}시간 범위 밖입니다: {bad}"
    return True, "체크리스트 기한 전부 72시간 이내"


def _rule6_stakeholder(ctx: _Context) -> tuple[bool, str]:
    expected = get(ctx.risk_type_code).stakeholder.value
    mismatched = [
        f"{s.get('title', '?')} -> {s.get('target_stakeholder')}"
        for s in ctx.report.get("strategies") or []
        if s.get("target_stakeholder") != expected
    ]
    if mismatched:
        return False, f"유형 {ctx.risk_type_code}의 담당 주체는 '{expected}'인데 다른 대상의 전략이 있습니다: {mismatched}"
    return True, f"전략 대상이 모두 '{expected}'"


def _rule7_regulation(ctx: _Context) -> tuple[bool, str]:
    tasks = " ".join(str(i.get("task", "")) for i in ctx.report.get("checklist") or [])
    missing = []
    for reg in ctx.evidence.regulations:
        if reg.deadline_hours is None:
            continue
        # 40일 지급 기한 같은 장기 의무는 72시간 체크리스트에 넣을 수 없다.
        # 규칙 5(기한 72시간 이내)와 충돌하므로 여기서 강제하지 않는다.
        if not reg.checklist_enforce:
            continue
        # 조항 번호나 법령명 중 하나라도 체크리스트에 언급돼 있으면 통과로 본다.
        if reg.article not in tasks and reg.law_name not in tasks:
            missing.append(f"{reg.law_name} {reg.article}({reg.deadline_hours}시간)")
    if missing:
        return False, f"기한 의무가 있는 법령이 체크리스트에 없습니다: {missing}"
    return True, "기한 의무 법령이 모두 체크리스트에 반영됨"


def _rule8_case_sources(ctx: _Context) -> tuple[bool, str]:
    """인용한 사례가 case_insights에 실제로 정리돼 있는지 검사한다.

    이전에는 '출처 URL이 본문에 있는지'를 봤는데, 출처 URL을 LLM이 본문에 쓰게 하면
    (1) 읽는 사람 입장에서 문장 중간에 URL이 끼어 가독성이 떨어지고 (2) URL 자체를 모델이
    잘못 옮겨 적을 여지가 남는다. 지금은 URL을 우리가 저장한 근거에서 화면에 직접 렌더링하고,
    본문에서는 사례가 제대로 정리됐는지만 검사한다 - 모델이 URL을 쓸 일이 없으니
    잘못 적을 일도 없다.
    """
    cited = set(ctx.report.get("cited_case_ids") or [])
    if not cited:
        return True, "인용 사례 없음"
    documented = {i.get("case_id") for i in ctx.report.get("case_insights") or []}
    missing = sorted(cited - documented)
    if missing:
        return False, f"인용했다고 표시한 사례가 case_insights에 정리되지 않았습니다: {missing}"
    unknown = sorted(documented - ctx.evidence.allowed_case_ids)
    if unknown:
        return False, f"수집 결과에 없는 사례가 case_insights에 있습니다: {unknown}"
    return True, f"인용 사례 {len(cited)}건이 모두 case_insights에 정리됨"


def _rule9_case_used(ctx: _Context) -> tuple[bool, str]:
    """사례가 수집됐는데 보고서에서 전혀 쓰지 않았는지 검사한다.

    근거를 모아놓고 안 쓰면 3단계(근거 수집)가 비용만 쓰고 보고서에 반영되지 않는다.
    """
    if not ctx.evidence.cases:
        return True, "수집된 사례 없음"
    insights = ctx.report.get("case_insights") or []
    if not insights:
        return False, f"사례 {len(ctx.evidence.cases)}건이 수집됐는데 하나도 활용하지 않았습니다"
    return True, f"수집 {len(ctx.evidence.cases)}건 중 {len(insights)}건 활용"


# 본문에 새어 나오면 안 되는 내부 표기. 읽는 사람은 주가·마케팅 담당자라, 이런 표기가
# 보이면 그 문장을 해석할 수 없다(프롬프트의 [읽는 사람] 규칙을 코드로 강제하는 검사다).
_INTERNAL_FIELD_NAMES = (
    "negative_ratio", "daily_growth_rate", "anomaly_score", "mention_count",
    "n_unique_channels", "diversity_ratio", "crisis_probability", "video_hhi",
    "escalation_tier", "spread_stage", "baseline_mean",
)
# 샘플 픽스처가 쓰는 표기. 프로덕션 id는 이 형태가 아니라 맨 숫자(str(article.id))다.
_SAMPLE_MENTION_ID_PATTERN = re.compile(r"\[m[_-]?\d+\]")


def _leaked_mention_ids(body: str, evidence: Evidence) -> list[str]:
    """본문에 새어 나온 원문 식별자를 찾는다.

    프로덕션 mention_id는 맨 숫자라, 숫자만 훑으면 "271건" 같은 정상 서술까지 걸린다.
    프롬프트가 원문을 `- [12345] ...` 형태로 보여 주므로 실제 id가 괄호에 싸인 경우만
    잡는다. 예전 픽스처 표기(`[m_1]`)도 함께 본다.
    """
    found = set(_SAMPLE_MENTION_ID_PATTERN.findall(body))
    for m in getattr(evidence, "mentions", []) or []:
        mid = str(getattr(m, "mention_id", "") or "")
        if not mid:
            continue
        for token in (f"[{mid}]", f"({mid})", f"[m_{mid}]"):
            if token in body:
                found.add(token)
    return sorted(found)


def _rule10_reader_facing(ctx: _Context) -> tuple[bool, str]:
    """지표 필드명·원문 식별자·URL이 본문에 노출됐는지 검사한다."""
    body_parts = [
        " ".join(ctx.report.get("summary_points") or []),
        str(ctx.report.get("judgment_basis", "")),
        " ".join(str(s.get("detail", "")) for s in ctx.report.get("strategies") or []),
        " ".join(str(i.get("insight", "")) for i in ctx.report.get("case_insights") or []),
        " ".join(
            str(r) for r in (ctx.report.get("risk_assessment") or {}).get("primary_risks", [])
        ),
    ]
    body = " ".join(body_parts)

    problems = []
    leaked = sorted({f for f in _INTERNAL_FIELD_NAMES if f in body})
    if leaked:
        problems.append(f"지표 필드명 노출: {leaked}")
    ids = _leaked_mention_ids(body, ctx.evidence)
    if ids:
        problems.append(f"원문 식별자 노출: {ids}")
    if "http://" in body or "https://" in body:
        problems.append("본문에 URL 노출(출처는 인용 근거 영역에만 표시)")

    if problems:
        return False, "; ".join(problems)
    return True, "필드명·원문번호·URL 노출 없음"


# (id, 이름, 지금 검사 가능한가, 검사 함수, 비활성 사유)
_RULES: list[tuple[int, str, Callable[[_Context], bool], Callable[[_Context], tuple[bool, str]], str]] = [
    (1, "사례 인용", lambda c: True, _rule1_cases, ""),
    (2, "원문 인용", lambda c: True, _rule2_mentions, ""),
    (3, "책임-전략 상충", lambda c: True, _rule3_responsibility, ""),
    (4, "개수 상한", lambda c: True, _rule4_counts, ""),
    (5, "기한 범위", lambda c: True, _rule5_deadline, ""),
    (6, "전략 대상", lambda c: True, _rule6_stakeholder, ""),
    (
        7,
        "법령 기한",
        lambda c: not c.evidence.no_regulation_mode,
        _rule7_regulation,
        "법령 매핑표가 비어 있어 검사할 수 없습니다(매핑표를 채우면 자동 활성화)",
    ),
    (
        8,
        "사례 정리",
        lambda c: bool(c.evidence.cases),
        _rule8_case_sources,
        "수집된 사례가 없어 검사 대상이 아닙니다",
    ),
    (
        9,
        "사례 활용",
        lambda c: bool(c.evidence.cases),
        _rule9_case_used,
        "수집된 사례가 없어 검사 대상이 아닙니다",
    ),
    (
        10,
        "독자 친화 표기",
        lambda c: True,
        _rule10_reader_facing,
        "",
    ),
]


def verify(report: dict[str, Any], evidence: Evidence, risk_type_code: str) -> VerifyResult:
    ctx = _Context(report=report, evidence=evidence, risk_type_code=risk_type_code)
    results: list[RuleResult] = []

    for rule_id, name, is_active, check, inactive_reason in _RULES:
        if not is_active(ctx):
            results.append(RuleResult(rule_id, name, "skip", inactive_reason))
            continue
        ok, message = check(ctx)
        results.append(RuleResult(rule_id, name, "pass" if ok else "fail", message))

    return VerifyResult(results=results)
