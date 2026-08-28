"""유형별 대응 원칙 11개 + 담당 주체별 커뮤니케이션 지침 5개.

검색되는 자료가 아니라 **프롬프트에 그대로 끼워 넣는 정적 텍스트**다(워크플로우 문서 §8-3).
지금 값은 전부 **임의로 작성한 초안**이다 - 문서가 제안한 대로 실패 사례에서 역산해 다시
써야 하며, 지금은 초안 생성 기능을 확인하는 게 목적이라 자리를 채워 둔 상태다.

작성 규칙(문서 §8-3):
- 각 200~400자. 길면 토큰을 먹고 LLM이 다 반영하지 못한다.
- "해야 할 것"보다 "하지 말아야 할 것"을 앞세운다. 금지문이 생성 제어에 훨씬 잘 먹힌다.

원칙 블록은 **나중에 RAG로 대체할 예정**이다. 그 교체 지점이 파일 하단의 PrincipleProvider다 -
지금은 이 파일의 상수를 그대로 쓰는 StaticPrincipleProvider가 기본값이고, 교재·사내 매뉴얼을
검색해 오는 구현으로 갈아끼우면 된다.

PROMPT_VERSION은 반려 사유 `전략_부적합`을 이 블록의 어느 버전에 매핑할지 추적하는 키다.
원칙 문구를 의미 있게 고칠 때마다 올릴 것 - 무엇을 고쳤을 때 반려율이 어떻게 변했는지
보려면 이 값이 보고서에 찍혀 있어야 한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .risk_types import Stakeholder

# 근거 기반 데이터가 붙은 뒤의 버전. 자료 없는 4개 유형은 여전히 draft 텍스트를 쓴다.
PROMPT_VERSION = "principles-v2.5-hierarchical"

# 유형 코드 -> 대응 원칙. 임의 초안이다.
# 근거 자료가 없는 세부 유형의 폴백 초안. principles_data.json에서 grounded=false인
# 것만 여기로 온다(현재 R03 산업재해 · R07 노무·고용 · R11 정산·거래조건).
# 근거 자료가 확보되면 principles_data.json으로 옮기고 여기서 지운다.
TYPE_PRINCIPLES: dict[str, str] = {
    "R03": (
        "중대재해는 법적 고용관계를 앞세운 해명이 가장 강한 역풍을 부른다. 계약 형태와 무관하게 "
        "실제 작업 환경에 대한 책임을 인정하고, 작업 중지와 재발 방지 조치를 먼저 밝힌다. "
        "하지 말 것: '위탁 계약이라 직접 책임이 없다'는 논리, 산재를 개인 부주의로 귀속시키는 것, "
        "사망·중대재해 사안에서 실적·성장 수치를 같은 문서에 배치하는 것."
    ),
    "R07": (
        "노무 갈등은 회사가 아니라 현장이 읽는다는 전제로 쓴다. 노조·노동자 대표가 있으면 공개 "
        "발표보다 협의 자리가 먼저이고, 합의된 것과 협의 중인 것을 구분해 밝힌다. 하지 말 것: "
        "교섭 중 사안을 언론에 먼저 흘리는 것, 문제를 제기한 직원에게 불이익이 갈 수 있다는 인상을 "
        "주는 것, 개별 사안으로 축소해 구조적 지적을 덮는 것."
    ),
    "R11": (
        "정산 이슈의 상대는 소비자가 아니라 생계가 걸린 판매자다. 공개 사과문보다 정산 일정표와 "
        "개별 소명 창구가 먼저이고, 지연 이자·보전 기준을 함께 낸다. 하지 말 것: 소비자용 톤(감성적 "
        "사과)을 그대로 쓰는 것, '시스템 개선 중'으로 지급 일정을 대체하는 것, 문제를 제기한 "
        "판매자에게 불이익이 갈 수 있다는 인상을 주는 것."
    ),
}

# 담당 주체 -> 커뮤니케이션 지침. 톤·채널·보상 수단이 갈리는 지점만 적는다.
STAKEHOLDER_GUIDES: dict[Stakeholder, str] = {
    Stakeholder.CONSUMER: (
        "채널은 앱 공지·이메일 등 이용자가 이미 보는 곳을 우선한다. 보도자료만 내고 앱에 아무것도 없으면 "
        "'언론에만 해명했다'는 지적을 받는다. 톤은 전문용어를 걷어낸 평서문. 보상은 금액보다 절차의 자동화가 "
        "중요하다 - 신청해야 받는 보상은 안 준 것으로 인식된다."
    ),
    Stakeholder.SELLER: (
        "판매자는 생계가 걸려 있어 감성적 사과보다 숫자와 일정이 먼저다. 채널은 셀러 어드민 공지와 "
        "전담 창구를 병행하고, 정산·수수료는 소급 적용 여부를 반드시 명시한다. 개별 소명 결과를 언제까지 "
        "회신하는지도 함께 적는다."
    ),
    Stakeholder.WORKER: (
        "노동자·라이더 대상 메시지는 회사가 아니라 현장이 읽는다는 전제로 쓴다. 노조·노동자 대표가 있으면 "
        "공개 발표보다 협의 자리가 먼저다. 안전·건강 사안은 작업 중단 권한을 명시하고, 불이익 없음을 "
        "구체적 조항으로 보장한다."
    ),
    Stakeholder.REGULATOR: (
        "규제기관·투자자 대상은 추정과 사실을 엄격히 분리한다. 조사 진행 중 사안에 결론적 표현을 쓰지 않고, "
        "공시 의무가 있으면 공시 문안과 대외 메시지의 표현을 일치시킨다. 불일치 자체가 별도 리스크가 된다."
    ),
    Stakeholder.PUBLIC: (
        "일반대중 대상은 도달 범위가 넓어 표현 하나가 그대로 인용된다. 확인된 사실과 확인 중인 사항을 시각적으로 "
        "분리하고, 반복 노출을 피하기 위해 부정문보다 사실 진술을 쓴다. 대응 여부 자체를 선택지로 두고 판단한다."
    ),
}


# ── 근거 기반 원칙 (principles_data.json) ────────────────────────────────
# docs/rag_sample의 수집 자료에서 유형별 원칙을 뽑아 둔 데이터다. 11개 유형 중 7개는
# 근거가 있고(T1·T3·T4·T7·T8·T10·T11), 4개는 해당 자료가 없어(T2·T5·T6·T9) 위의 초안
# 텍스트를 그대로 쓴다. 자료가 없는 유형을 억지로 채우지 않는 것이 이 파일의 규칙이다.
#
# **왜 런타임 벡터 검색(RAG)이 아니라 유형 코드 조회인가**
#   1. 원칙은 유사도로 찾을 대상이 아니다. 유형은 이미 확정된 채로 들어오고, 그 유형의
#      원칙은 항상 같은 것이 들어가야 한다. top-k로 매번 다른 청크가 들어오면
#      prompt_version으로 반려율 변화를 추적하는 설계가 무너진다.
#   2. 원문을 그대로 프롬프트에 넣으면 위험하다. 리콜 자료는 미국 CPSC 신고 절차와
#      16 CFR 조항이 본문에 박혀 있고, 사이버 사고 자료는 NIST 프레임워크 용어로
#      서술돼 있으며, 고객대응 자료는 공중보건 재난 사례가 배경이다. 그대로 인용되면
#      국내 기업 보고서에 미국 절차가 실리거나, 담당자가 읽을 수 없는 용어가 나온다.
#      각 유형의 caution 필드가 그 경계를 적어 둔 것이다.
#   3. 보고서 1건마다 임베딩 검색과 긴 청크 삽입이 붙는 비용·지연이 생긴다.
#
# 다만 RAG로 갈 길은 막지 않았다. 각 원칙에 출처 문서·페이지를 달아 뒀으므로, 나중에
# "이번 상황에 맞는 추가 지침"을 벡터로 검색해 얹으려면 PrincipleProvider 구현만
# 바꾸면 된다(파일 하단 참고).

_DATA_PATH = Path(__file__).with_name("principles_data.json")
_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        with open(_DATA_PATH, encoding="utf-8") as fp:
            _data = json.load(fp)
    return _data


def _render_block(entry: dict) -> str:
    """must / must_not / caution 을 프롬프트에 넣을 텍스트로 편다."""
    parts = []
    if entry.get("must"):
        parts.append("반드시 지킬 것:\n" + "\n".join(f"  - {m}" for m in entry["must"]))
    if entry.get("must_not"):
        parts.append("하지 말 것:\n" + "\n".join(f"  - {m}" for m in entry["must_not"]))
    if entry.get("caution"):
        parts.append(f"근거 자료의 한계: {entry['caution']}")
    return "\n".join(parts)


def common_base() -> str:
    """전 유형에 공통으로 들어가는 위기 커뮤니케이션 베이스.

    유형별 자료가 없는 4개 유형(T2·T5·T6·T9)에도 이 블록은 들어간다 - Coombs와 CERC는
    특정 업종이 아니라 조직 위기 일반을 다루므로, 그 유형에 자료가 없다는 것과 이 원칙이
    적용되지 않는다는 것은 다른 이야기다.
    """
    return _render_block(_load()["common_base"])


def grounded_types() -> list[str]:
    return [k for k, v in _load()["types"].items() if v.get("grounded")]


def sources_for(risk_type_code: str) -> list[dict]:
    """이 유형의 원칙이 어느 문서 몇 페이지에서 왔는지. 보고서 이력에 남긴다."""
    return _load()["types"].get(risk_type_code, {}).get("sources", [])


def principle_for(risk_type_code: str) -> str:
    """유형별 대응 원칙. 근거 자료가 있으면 그것을, 없으면 위의 초안 텍스트를 쓴다."""
    entry = _load()["types"].get(risk_type_code)
    if entry and entry.get("grounded"):
        return _render_block(entry)
    fallback = TYPE_PRINCIPLES.get(risk_type_code)
    if fallback:
        return fallback
    return "(해당 유형의 대응 원칙 블록이 아직 작성되지 않았습니다.)"


def guide_for(stakeholder: Stakeholder) -> str:
    return STAKEHOLDER_GUIDES.get(stakeholder, "(해당 주체의 커뮤니케이션 지침이 아직 작성되지 않았습니다.)")


class PrincipleProvider(Protocol):
    """유형별 대응 원칙과 주체별 지침을 공급하는 인터페이스.

    지금은 이 파일의 상수를 그대로 돌려주는 StaticPrincipleProvider가 기본값이다.
    **나중에 RAG로 바꿀 자리가 여기다** - 교재 PDF나 사내 위기관리 매뉴얼을 임베딩해
    "이번 유형·이번 상황에 맞는 원칙 조각"을 검색해 오는 구현으로 갈아끼우면 되고,
    generate.py는 손대지 않는다.

    RAG로 갈 때 주의할 점 하나: 원칙은 사례와 성격이 다르다. 사례는 없으면 인용을 금지하면
    그만이지만(no_case_mode), **원칙은 비면 LLM이 아무 기준 없이 쓰게 된다.** 그래서 RAG
    검색이 실패했을 때 빈 문자열을 돌려주지 말고, 최소한 이 파일의 정적 원칙으로 폴백해야
    한다 - ChainedPrincipleProvider가 그 형태다.
    """

    def principle_for(self, risk_type_code: str) -> str:
        ...

    def guide_for(self, stakeholder: Stakeholder) -> str:
        ...

    @property
    def version(self) -> str:
        ...


class StaticPrincipleProvider:
    """이 파일에 하드코딩된 원칙을 그대로 쓰는 기본 구현."""

    @property
    def version(self) -> str:
        return PROMPT_VERSION

    def principle_for(self, risk_type_code: str) -> str:
        return principle_for(risk_type_code)

    def guide_for(self, stakeholder: Stakeholder) -> str:
        return guide_for(stakeholder)


class ChainedPrincipleProvider:
    """RAG 검색 결과를 먼저 쓰고, 비면 정적 원칙으로 폴백한다.

    RAG를 붙일 때 이 형태로 감싸면 검색 실패가 곧 원칙 없는 생성으로 이어지지 않는다.
    """

    def __init__(self, primary: PrincipleProvider, fallback: PrincipleProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or StaticPrincipleProvider()

    @property
    def version(self) -> str:
        return f"{self.primary.version}+fallback:{self.fallback.version}"

    def principle_for(self, risk_type_code: str) -> str:
        return self.primary.principle_for(risk_type_code).strip() or self.fallback.principle_for(risk_type_code)

    def guide_for(self, stakeholder: Stakeholder) -> str:
        return self.primary.guide_for(stakeholder).strip() or self.fallback.guide_for(stakeholder)