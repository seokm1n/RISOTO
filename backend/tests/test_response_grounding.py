"""대응 초안이 입력으로 허용한 URL 밖의 주장을 인용하지 않는지 검증한다."""

import unittest
from types import SimpleNamespace

from app.services.response_generation import (
    MAIN_RESPONSE,
    COMPETITOR_IMPACT,
    _filter_citations,
    _template_content,
)


class ResponseGroundingTests(unittest.TestCase):
    def test_unknown_citations_and_uncited_actions_are_removed(self):
        allowed = "https://news.example/evidence"
        content = {
            "evidence": [
                {"title": "근거", "url": allowed},
                {"title": "환각", "url": "https://unknown.example"},
            ],
            "precedents": [{"title": "환각 사례", "url": "https://unknown.example"}],
            "recommended_actions": {
                "immediate": [
                    {"action": "확인", "evidence_urls": [allowed, "https://unknown.example"]},
                    {"action": "근거 없는 실행", "evidence_urls": []},
                ]
            },
        }
        result = _filter_citations(content, {allowed})
        self.assertEqual([item["url"] for item in result["evidence"]], [allowed])
        self.assertEqual(result["precedents"], [])
        self.assertEqual(len(result["recommended_actions"]["immediate"]), 1)
        self.assertEqual(result["recommended_actions"]["immediate"][0]["evidence_urls"], [allowed])

    def test_nested_scenario_citations_are_filtered_recursively(self):
        allowed = "https://news.example/evidence"
        content = {
            "evidence": [{"title": "근거", "url": allowed}],
            "precedents": [],
            "scenarios": [
                {
                    "title": "영향",
                    "recommended_actions": {
                        "immediate": [
                            {
                                "action": "허용된 근거 확인",
                                "evidence_urls": [
                                    allowed,
                                    "http://unknown.example/evidence",
                                    "javascript:alert(1)",
                                ],
                            },
                            {
                                "action": "허용되지 않은 근거",
                                "evidence_urls": ["javascript:alert(1)"],
                            },
                        ]
                    },
                }
            ],
        }

        result = _filter_citations(
            content,
            {allowed, "javascript:alert(1)"},
        )

        actions = result["scenarios"][0]["recommended_actions"]["immediate"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["evidence_urls"], [allowed])

    def test_deterministic_templates_have_two_kind_specific_scenarios(self):
        event = SimpleNamespace(summary="위험 요약")
        source = SimpleNamespace(name="경쟁사", company_role="competitor")
        main = SimpleNamespace(name="메인사", company_role="main")
        evidence = [{"title": "근거", "url": "https://news.example/evidence"}]

        main_content = _template_content(
            event,
            ["reputation_consumer"],
            evidence,
            [],
            main,
            main,
            MAIN_RESPONSE,
        )
        competitor_content = _template_content(
            event,
            ["reputation_consumer"],
            evidence,
            [],
            source,
            main,
            COMPETITOR_IMPACT,
        )

        self.assertEqual(len(main_content["scenarios"]), 2)
        self.assertIn("rationale", main_content["scenarios"][0])
        self.assertEqual(len(competitor_content["scenarios"]), 2)
        self.assertIn("possible_impact", competitor_content["scenarios"][0])
        self.assertIn("early_indicators", competitor_content["scenarios"][0])


if __name__ == "__main__":
    unittest.main()
