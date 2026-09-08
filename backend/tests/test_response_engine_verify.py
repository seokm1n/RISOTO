"""초안이 수집한 근거 밖을 인용하지 않는지 검증한다.

예전에는 response_generation._filter_citations가 생성 결과에서 허용 URL 밖의 인용을
사후에 지워냈다(test_response_grounding.py). response_engine은 지우는 대신 **검증에서
걸러 재생성**한다 - 근거가 없는데도 문장이 남는 편보다, 왜 걸렸는지 남기고 다시 쓰게
하는 편이 사람이 판단하기 쉽기 때문이다. 그 방어선이 규칙 1(사례)과 규칙 2(원문)다.
"""

import unittest

from app.services.response_engine.evidence import Evidence
from app.services.response_engine.retrieval import PastCase
from app.services.response_engine.schema import Mention
from app.services.response_engine.verify import verify


def _evidence(*, with_case: bool = True) -> Evidence:
    cases = [
        PastCase(
            case_id="case-1",
            title="유사 사례",
            risk_type="R01",
            outcome="성공",
            summary_what="리콜 후 회수",
            source_urls=["https://news.example/case-1"],
        )
    ] if with_case else []
    return Evidence(
        mentions=[Mention(mention_id="m-1", text="원문", url="https://news.example/m-1")],
        cases=cases,
        no_case_mode=not with_case,
    )


def _report(**overrides) -> dict:
    report = {
        "cited_case_ids": [],
        "cited_mention_ids": [],
    }
    report.update(overrides)
    return report


def _failed_rules(result) -> set[int]:
    return {r.rule_id for r in result.results if r.status == "fail"}


class ResponseEngineCitationRules(unittest.TestCase):
    def test_citations_inside_collected_evidence_pass(self):
        result = verify(
            _report(cited_case_ids=["case-1"], cited_mention_ids=["m-1"]),
            _evidence(),
            "R01",
        )
        self.assertNotIn(1, _failed_rules(result))
        self.assertNotIn(2, _failed_rules(result))

    def test_unknown_case_id_is_rejected(self):
        result = verify(_report(cited_case_ids=["없는-사례"]), _evidence(), "R01")
        self.assertIn(1, _failed_rules(result))
        self.assertTrue(any("없는-사례" in v for v in result.violations))

    def test_unknown_mention_id_is_rejected(self):
        result = verify(_report(cited_mention_ids=["m-999"]), _evidence(), "R01")
        self.assertIn(2, _failed_rules(result))
        self.assertTrue(any("m-999" in v for v in result.violations))

    def test_no_case_mode_forbids_any_case_citation(self):
        """사례를 하나도 못 찾았을 때 모델이 아는 사례를 끌어오는 것을 막는다."""
        result = verify(
            _report(cited_case_ids=["case-1"]),
            _evidence(with_case=False),
            "R01",
        )
        self.assertIn(1, _failed_rules(result))


if __name__ == "__main__":
    unittest.main()
