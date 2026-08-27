"""리스크 유형 체계 — 탐지 8개(상위) / 대응 13개(하위)의 2계층.

**왜 두 계층인가**
탐지와 대응은 요구하는 해상도가 다르다. 탐지는 학습 모델이라 클래스를 늘리면 클래스당
표본이 줄어 성능이 떨어지고, 유형이 피처 벡터에 들어가므로 차원만 늘고 희소해진다.
반면 대응은 학습이 아니라 원칙 블록을 고르는 조회라, 클래스를 늘려도 비용이 없고
정확도는 곧바로 올라간다.

구체적으로: 배송 지연과 정산 지연은 탐지 신호로는 둘 다 "운영 차질"이지만, 대응은
상대(소비자 vs 판매자)도 수단(환불 vs 지급 일정)도 정반대다. 여기서 뭉뚱그리면
판매자에게 소비자용 사과문을 보내게 된다.

**엄격한 계층이다.** 각 세부 유형의 상위는 정확히 하나이며, 상위 8개는 팀 저장소의
`app/risk_taxonomy.py`와 동일한 코드를 쓴다. 탐지 모델·라벨·프론트엔드는 건드리지 않는다.

**담당 주체는 축이 아니라 파생값이다.** 세부 유형이 정해지면 주체는 조회로 나온다.
별도로 판정할 필요가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stakeholder(str, Enum):
    """대응 커뮤니케이션의 1차 수신자. principles.py의 지침 블록 선택 키."""

    CONSUMER = "소비자"
    SELLER = "판매자·입점사"
    WORKER = "노동자·라이더"
    REGULATOR = "규제기관·투자자"
    PUBLIC = "일반대중"


class Sensitivity(str, Enum):
    """대응 등급 매트릭스(tier.py)의 열 인덱스."""

    HIGH = "높음"
    MEDIUM = "중간"
    LOW = "낮음"


# 탐지 계층(상위 8개). 팀 저장소 app/risk_taxonomy.py의 RISK_TYPES와 코드가 같아야 한다.
DETECTION_TYPES: dict[str, str] = {
    "product_quality": "제품·품질",
    "safety_accident": "안전·사고",
    "security_privacy": "보안·개인정보",
    "legal_regulatory": "법률·규제",
    "labor_hr": "노동·인사",
    "financial_governance": "재무·지배구조",
    "supply_operations": "공급·운영",
    "reputation_consumer": "평판·소비자",
}


@dataclass(frozen=True)
class RiskType:
    code: str
    label: str
    parent: str                      # 탐지 계층의 상위 유형 (정확히 하나)
    stakeholder: Stakeholder
    sensitivity: Sensitivity
    scope: str
    secondary_stakeholders: tuple[Stakeholder, ...] = field(default_factory=tuple)


RISK_TYPES: tuple[RiskType, ...] = (
    RiskType("R01", "품질·결함", "product_quality", Stakeholder.CONSUMER, Sensitivity.MEDIUM,
             "제품 결함·이물질·불량·리콜·유통기한·표시 위반"),
    RiskType("R02", "소비자 안전사고", "safety_accident", Stakeholder.CONSUMER, Sensitivity.HIGH,
             "제품·시설로 인한 상해·화재·중독 등 이용자 안전 피해"),
    # R03과 R02는 같은 상위(safety_accident)지만 대응이 정반대다 - 하나는 소비자에게
    # 사용 중단을 알리는 일이고, 하나는 작업 중지와 노동부 보고가 먼저다.
    RiskType("R03", "산업재해", "safety_accident", Stakeholder.WORKER, Sensitivity.HIGH,
             "작업 중 사고·중대재해·과로·산업안전 위반"),
    RiskType("R04", "개인정보·보안", "security_privacy", Stakeholder.CONSUMER, Sensitivity.HIGH,
             "개인정보 유출·해킹·과다 수집·계정 도용·보안 취약점",
             (Stakeholder.REGULATOR,)),
    RiskType("R05", "가격·약관·표시", "legal_regulatory", Stakeholder.CONSUMER, Sensitivity.LOW,
             "다크패턴·멤버십 인상·기만 광고·부당 표시·환불 약관"),
    RiskType("R06", "규제제재·소송", "legal_regulatory", Stakeholder.REGULATOR, Sensitivity.MEDIUM,
             "공정위·검찰 조사, 과징금, 소송, 불공정거래 제재"),
    RiskType("R07", "노무·고용", "labor_hr", Stakeholder.WORKER, Sensitivity.MEDIUM,
             "임금·해고·노조 갈등·직장 내 괴롭힘·위장 도급"),
    RiskType("R08", "재무·지배구조", "financial_governance", Stakeholder.REGULATOR, Sensitivity.MEDIUM,
             "횡령·배임·회계 문제·오너리스크·지배구조·실적 악화"),
    RiskType("R09", "배송·물류", "supply_operations", Stakeholder.CONSUMER, Sensitivity.LOW,
             "배송 지연·파손·오배송·분실·물류센터 운영 문제"),
    RiskType("R10", "서비스장애·기술", "supply_operations", Stakeholder.CONSUMER, Sensitivity.LOW,
             "접속 불가·앱 오류·결제 실패·데이터 오류·시스템 장애"),
    # R11이 같은 상위(supply_operations)의 R09·R10과 갈리는 지점이 담당 주체다.
    # 탐지 신호로는 셋 다 "운영 차질"이지만 대응 상대가 소비자가 아니라 판매자다.
    RiskType("R11", "정산·거래조건", "supply_operations", Stakeholder.SELLER, Sensitivity.MEDIUM,
             "판매자 정산 지연·수수료 인상·일방적 계약 변경·입점 제한"),
    RiskType("R12", "고객대응·환불", "reputation_consumer", Stakeholder.CONSUMER, Sensitivity.LOW,
             "환불 거부·환불 지연·이중 결제·상담 불가·응대 태도·처리 지연"),
    # R13은 부인(denial)이 적절한 유일한 유형이다. 같은 상위의 R12(환불 분쟁)에서는
    # 부인이 최악의 선택이라, 이 둘을 갈라놓지 않으면 정반대 원칙이 섞인다.
    RiskType("R13", "평판·루머", "reputation_consumer", Stakeholder.PUBLIC, Sensitivity.MEDIUM,
             "미확인 소문·허위사실 유포·비방·유언비어·근거 없는 의혹 확산"),
)

BY_CODE: dict[str, RiskType] = {t.code: t for t in RISK_TYPES}
CODES: tuple[str, ...] = tuple(t.code for t in RISK_TYPES)

# 상위 유형 -> 하위 후보. 탐지 결과가 오면 이 목록 안에서만 세부 유형을 고른다.
CHILDREN: dict[str, tuple[str, ...]] = {}
for _t in RISK_TYPES:
    CHILDREN.setdefault(_t.parent, ())
    CHILDREN[_t.parent] = CHILDREN[_t.parent] + (_t.code,)


def get(code: str) -> RiskType:
    if code not in BY_CODE:
        raise KeyError(f"알 수 없는 리스크 유형 코드: {code} (사용 가능: {', '.join(CODES)})")
    return BY_CODE[code]


def children_of(detection_type: str) -> tuple[str, ...]:
    """탐지 유형의 세부 후보. 1개면 LLM 판정 없이 그대로 확정한다."""
    return CHILDREN.get(detection_type, ())


def stakeholder_of(code: str) -> Stakeholder:
    """담당 주체는 세부 유형에서 조회로 나온다. 별도 판정 축이 아니다."""
    return get(code).stakeholder


def catalog_for_prompt(candidates: tuple[str, ...] | None = None) -> str:
    """LLM 세부 분류 프롬프트에 넣을 목록. 후보를 주면 그 안에서만 고르게 한다."""
    items = [BY_CODE[c] for c in candidates] if candidates else list(RISK_TYPES)
    return "\n".join(f"- {t.code} {t.label}: {t.scope}" for t in items)
