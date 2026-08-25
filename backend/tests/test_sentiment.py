"""NLI 확률을 기업 뉴스 감성으로 변환하는 로직의 회귀 테스트."""

import unittest
from unittest.mock import patch

from app.services.sentiment import KlueRobertaSentimentAnalyzer


class FakeNli:
    """실제 모델 없이 감성 매핑을 검증하는 고정 출력 NLI 대역."""

    def score_hypotheses(self, premises, hypotheses, **kwargs):
        """긍정·부정·중립 분기 검증용 고정 확률을 반환한다."""
        return [
            [0.15, 0.85],
            [0.80, 0.20],
            [0.46, 0.54],
        ]


class KlueRobertaSentimentTests(unittest.TestCase):
    """긍정·부정·중립 경계와 점수 방향을 검증한다."""

    @patch("app.services.sentiment.get_klue_nli_classifier", return_value=FakeNli())
    def test_positive_negative_and_neutral_mapping(self, _get_classifier):
        """NLI 확률이 긍정·부정·중립 레이블과 점수로 올바르게 매핑되는지 검증한다."""
        analyzer = KlueRobertaSentimentAnalyzer("test-model", False)
        results = analyzer.analyze(["호재", "악재", "공지"])
        self.assertEqual([item.label for item in results], ["긍정", "부정", "중립"])
        self.assertGreater(results[0].score, 0)
        self.assertLess(results[1].score, 0)
        self.assertAlmostEqual(results[2].score, 0.08)


if __name__ == "__main__":
    unittest.main()
