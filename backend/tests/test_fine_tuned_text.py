"""Local Hugging Face artifact label mapping regression tests."""

from types import SimpleNamespace
import unittest

from app.services.fine_tuned_text import (
    RELEVANCE_INPUT_SCHEMA,
    RELEVANCE_MAX_LENGTH,
    _label_probabilities,
    format_relevance_input,
)


class FineTunedTextTests(unittest.TestCase):
    def test_sentiment_probabilities_follow_model_config_labels(self):
        model = SimpleNamespace(
            config=SimpleNamespace(id2label={0: "negative", 1: "neutral", 2: "positive"})
        )

        result = _label_probabilities(model, [0.7, 0.2, 0.1])

        self.assertEqual(result, {"negative": 0.7, "neutral": 0.2, "positive": 0.1})

    def test_relevance_probabilities_follow_model_config_labels(self):
        model = SimpleNamespace(
            config=SimpleNamespace(id2label={0: "normal", 1: "filter"})
        )

        result = _label_probabilities(model, [0.8, 0.2])

        self.assertEqual(result, {"normal": 0.8, "filter": 0.2})

    def test_relevance_input_includes_target_company_and_article(self):
        result = format_relevance_input(
            " 쿠팡 ",
            " 쿠팡플레이 신작의 구독 방법을 소개합니다. ",
        )

        self.assertEqual(
            result,
            "기업명: 쿠팡 제목 및 내용: 쿠팡플레이 신작의 구독 방법을 소개합니다.",
        )
        self.assertEqual(RELEVANCE_INPUT_SCHEMA, "company-title-content-v1")
        self.assertEqual(RELEVANCE_MAX_LENGTH, 128)

    def test_relevance_input_requires_both_company_and_article(self):
        self.assertEqual(format_relevance_input("", "기사"), "")
        self.assertEqual(format_relevance_input("쿠팡", ""), "")


if __name__ == "__main__":
    unittest.main()
