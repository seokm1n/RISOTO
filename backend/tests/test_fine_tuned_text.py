"""Local Hugging Face artifact label mapping regression tests."""

from types import SimpleNamespace
import unittest

from app.services.fine_tuned_text import _label_probabilities


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


if __name__ == "__main__":
    unittest.main()
