"""과거 사례 / 법령 검색기 인터페이스 + 현재 기본 구현(빈 결과).

**지금 비어 있는 이유**: 문서 §8-1·8-2가 요구하는 자산(과거 사례 40건, 법령 매핑표 20~40개)이
아직 없다. 그래서 초안 생성을 먼저 확인할 수 있도록 프롬프트 위주로 돌리되, 나중에 교재 PDF와
유사 케이스를 넣을 때 **이 파일의 클래스 두 개만 갈아 끼우면** 되도록 경계를 그어 놨다.

확장 시 해야 할 일:
  1. past_cases 테이블(부록 B의 DDL)을 만들고 pgvector로 임베딩 검색하는 CaseRetriever 구현
  2. regulations 매핑표를 채우고 RegulationMapper 구현 - **유사도가 아니라 결정적 조회**다.
     법령을 유사도로 찾지 않는 이유는 문서 §5에 있다: 개인정보 유출이면 통지·신고 의무가
     유사도와 무관하게 무조건 적용되는데, 상위 k개로 뽑으면 반드시 적용돼야 할 조항이
     우연히 빠질 수 있다.
  3. pipeline.py의 build_evidence 호출부에 새 구현을 주입 (다른 코드는 안 건드려도 됨)

검색기가 빈 결과를 주면 evidence.py가 no_case_mode / no_regulation_mode 플래그를 세우고,
generate.py가 프롬프트에서 인용을 금지하며, verify.py의 규칙 1이 "정말 인용 0건인지"를
검사한다. 즉 자산이 없는 상태가 조용히 넘어가지 않고 파이프라인 전체에 명시적으로 전파된다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol


@dataclass
class PastCase:
    case_id: str
    title: str
    risk_type: str
    outcome: str            # 성공 | 실패 | 혼재
    summary_what: str
    summary_response: str = ""
    summary_result: str = ""
    lesson: str = ""
    source_urls: list[str] = field(default_factory=list)
    # 이 사례가 어디서 왔는지. "curated"는 사람이 검수해 DB에 넣은 것, "web_search"는
    # 런타임에 검색해 온 것이다. 신뢰 수준이 다르므로 프롬프트와 검증(규칙 8)에서
    # 구분해서 다루고, 담당자 검토 화면에서도 구분되어야 한다.
    provenance: str = "curated"


@dataclass
class Regulation:
    reg_id: str
    law_name: str
    article: str
    requirement: str
    duty_type: str | None = None
    deadline_hours: int | None = None
    authority: str | None = None
    source_url: str | None = None
    # 아직 시행되지 않은 조문인지. True면 의무가 아니라 "곧 시행되니 대비하라"는 추천으로만 쓴다.
    is_upcoming: bool = False
    effective_from: str | None = None
    # 기한이 72시간을 넘어 위기 대응 체크리스트 항목이 될 수 없는 조문(예: 40일 지급 기한).
    # 검증 규칙 7이 체크리스트 반영을 강제하지 않고, 판정 기준선으로만 제공한다.
    checklist_enforce: bool = True
    # 적용 대상 요건(예: 원사업자·통신판매업자 해당 여부). 회사마다 달라 사람이 확인해야 한다.
    applicability_note: str | None = None
    # 서로 배타적인 조문 묶음. 유가증권시장과 코스닥시장 공시규정처럼 한 회사가 동시에
    # 적용받을 수 없는 조문들이 있다. 이 값이 같으면 그중 하나만 지키면 되므로, 검증
    # 규칙 7이 전부를 강제하지 않는다. applicability_note는 사람이 읽는 안내라
    # 기계가 판단할 수 없어 별도 필드로 둔다.
    exclusive_group: str | None = None


class CaseRetriever(Protocol):
    def search(self, risk_type: str, query_text: str, top_k: int = 3) -> list[PastCase]:
        ...


class RegulationMapper(Protocol):
    def lookup(self, risk_type: str) -> list[Regulation]:
        ...


class NullCaseRetriever:
    """사례 DB가 채워지기 전까지의 기본값. 항상 빈 목록을 준다."""

    def search(self, risk_type: str, query_text: str, top_k: int = 3) -> list[PastCase]:
        return []


class NullRegulationMapper:
    """법령 매핑표가 채워지기 전까지의 기본값. 항상 빈 목록을 준다."""

    def lookup(self, risk_type: str) -> list[Regulation]:
        return []


class StaticCaseRetriever:
    """유사도 계산 없이 유형으로만 필터링하는 중간 단계 구현.

    사례 몇 건을 손으로 넣어 프롬프트에 실제로 어떻게 반영되는지 보고 싶을 때 쓴다.
    임베딩 인프라 없이도 동작하므로, pgvector를 붙이기 전 단계에서 유용하다.
    """

    def __init__(self, cases: list[PastCase]) -> None:
        self._cases = cases

    def search(self, risk_type: str, query_text: str, top_k: int = 3) -> list[PastCase]:
        matched = [c for c in self._cases if c.risk_type == risk_type]
        return matched[:top_k]


class StaticRegulationMapper:
    """유형 -> 조항 매핑을 딕셔너리로 들고 있는 구현. 문서 §8-2가 말하는 결정적 조회 방식."""

    def __init__(self, mapping: dict[str, list[Regulation]]) -> None:
        self._mapping = mapping

    def lookup(self, risk_type: str) -> list[Regulation]:
        return list(self._mapping.get(risk_type, []))


class KoreanRegulationMapper:
    """국내 법령 매핑표(regulations_data.json)를 유형 코드로 결정적 조회한다.

    **시행일 기준을 조회 시점으로 잡는 이유**: 원본 저장소(legalize-kr)의 main은 공포된
    최신 개정본을 담고 있어서, 시행일이 아직 오지 않은 조문이 섞여 있다. 실제로 산업안전
    보건법 제54조는 시행일이 2027-01-08, 식품위생법 제45조는 2029-01-01이다. 이걸 그대로
    쓰면 "아직 시행되지 않은 조문을 지금의 법적 의무"로 안내하게 된다.

    그래서 두 갈래로 나눈다.
      - 시행 중(effective_from <= as_of)  -> 의무. 검증 규칙 7이 체크리스트 반영을 강제한다.
      - 시행 예정(effective_from > as_of) -> 참고. include_upcoming=True일 때만 딸려 나오고,
        is_upcoming=True가 붙어 프롬프트에서 "곧 시행될 사항이니 미리 대비하라"는 추천으로만
        쓰인다. 의무로 강제되지 않는다.

    **verified=False는 아예 서빙하지 않는다.** 기한 숫자와 적용 범위는 사람이 조문 원문과
    대조해야 하는 값이라(법률에는 '지체 없이'만 있고 72시간은 시행령에 있는 식으로 어긋난다),
    미확인 조문이 보고서에 인용되면 잘못된 법적 기한을 강제하게 된다.
    """

    def __init__(
        self,
        data_path: str | Path | None = None,
        as_of: date | None = None,
        include_upcoming: bool = True,
    ) -> None:
        self.path = Path(data_path) if data_path else Path(__file__).with_name("regulations_data.json")
        self.as_of = as_of or date.today()
        self.include_upcoming = include_upcoming
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            with open(self.path, encoding="utf-8") as fp:
                self._data = json.load(fp)
        return self._data

    def _in_force(self, row: dict) -> bool | None:
        eff = row.get("effective_from")
        if not eff:
            return None
        try:
            return date.fromisoformat(eff) <= self.as_of
        except ValueError:
            return None

    def lookup(self, risk_type: str) -> list[Regulation]:
        block = self._load().get("types", {}).get(risk_type)
        if not block:
            return []
        out: list[Regulation] = []
        for row in block.get("regulations", []):
            if not row.get("verified"):
                continue
            in_force = self._in_force(row)
            if in_force is False and not self.include_upcoming:
                continue
            out.append(
                Regulation(
                    reg_id=row["reg_id"],
                    law_name=row["law_name"],
                    article=row["article"],
                    requirement=row.get("requirement", ""),
                    duty_type=row.get("duty_type"),
                    # 시행 전 조문은 기한을 넘기지 않는다. 기한이 있으면 검증 규칙 7이
                    # 체크리스트 반영을 강제하는데, 아직 의무가 아닌 것을 강제하면 안 된다.
                    deadline_hours=row.get("deadline_hours") if in_force is not False else None,
                    authority=row.get("authority"),
                    source_url=row.get("source_url"),
                    is_upcoming=(in_force is False),
                    effective_from=row.get("effective_from"),
                    checklist_enforce=row.get("checklist_enforce", True),
                    applicability_note=row.get("applicability_note"),
                    exclusive_group=row.get("exclusive_group"),
                )
            )
        return out

    def stats(self, risk_type: str) -> dict:
        """이 유형에 몇 건이 있고 그중 몇 건이 검증·시행 상태인지. 운영 점검용."""
        rows = self._load().get("types", {}).get(risk_type, {}).get("regulations", [])
        return {
            "total": len(rows),
            "verified": sum(1 for r in rows if r.get("verified")),
            "in_force": sum(1 for r in rows if r.get("verified") and self._in_force(r) is not False),
            "upcoming": sum(1 for r in rows if r.get("verified") and self._in_force(r) is False),
        }
