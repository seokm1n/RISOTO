"""대응 초안이 입력으로 허용한 URL 밖의 주장을 인용하지 않는지 검증한다."""

import unittest

from app.services.response_generation import _filter_citations


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


if __name__ == "__main__":
    unittest.main()
