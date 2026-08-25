"""스토리 군집, 15분 특징과 데이터 분할의 핵심 계약 테스트."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.config import Settings
from app.services.risk_analysis import (
    BASE_FEATURE_NAMES,
    RiskDetectorRuntime,
    _numeric_features,
    _robust_z,
    classify_risk_types,
    enrich_risk_types_with_nli,
    resolve_risk_type_scores,
    score_window,
    update_risk_events,
)
from app.services.story_clustering import story_similarity
from app.training.common import chronological_group_split


class StoryAndFeatureTests(unittest.TestCase):
    def test_shared_model_features_have_types_but_no_company_id(self):
        self.assertNotIn("company_id", BASE_FEATURE_NAMES)
        self.assertNotIn("company", BASE_FEATURE_NAMES)
        self.assertEqual(
            len([name for name in BASE_FEATURE_NAMES if name.startswith("risk_type_")]),
            8,
        )

    def test_similar_headlines_share_a_high_story_score(self):
        score = story_similarity("쿠팡 배송 사고 발생", "속보 쿠팡 배송 사고 발생")
        self.assertGreaterEqual(score, 0.75)

    def test_numeric_features_distinguish_partial_and_valid_empty(self):
        values = _numeric_features(
            article_count=0,
            story_count=0,
            amplification_count=0,
            publisher_count=0,
            positive_probability=None,
            neutral_probability=None,
            negative_probability=None,
            negative_probability_p90=None,
            risk_keyword_ratio=0.0,
            risk_keyword_story_ratio=0.0,
            source_diversity=0.0,
            publisher_concentration=0.0,
            collection_completeness=0.5,
            previous=None,
            data_quality="partial",
        )
        self.assertEqual(values["no_article_flag"], 1.0)
        self.assertEqual(values["partial_source_flag"], 1.0)
        self.assertEqual(values["collection_completeness"], 0.5)

    def test_unavailable_window_never_scores(self):
        window = SimpleNamespace(data_quality="unavailable", model_state="production", scored_at="old")
        score_window(SimpleNamespace(), window, SimpleNamespace())
        self.assertEqual(window.model_state, "unavailable")
        self.assertIsNone(window.scored_at)

    @patch("app.services.risk_analysis.resolve_production_risk_detector")
    def test_missing_production_lightgbm_clears_scores_and_cannot_open_event(
        self,
        resolve_detector,
    ):
        resolve_detector.return_value = RiskDetectorRuntime(
            version=None,
            payload=None,
            reason="production_lightgbm_not_registered",
        )
        window = SimpleNamespace(
            data_quality="complete",
            anomaly_score=0.91,
            anomaly_percentile=0.99,
            risk_probability=0.94,
            decision_threshold=0.65,
            is_risk=True,
            model_state="production",
            model_version="stale-model",
            scored_at="stale-time",
        )

        score_window(SimpleNamespace(), window, SimpleNamespace())

        self.assertIsNone(window.anomaly_score)
        self.assertIsNone(window.anomaly_percentile)
        self.assertIsNone(window.risk_probability)
        self.assertIsNone(window.decision_threshold)
        self.assertFalse(window.is_risk)
        self.assertEqual(window.model_state, "unavailable")
        self.assertIsNone(window.model_version)
        self.assertIsNone(window.scored_at)
        db = SimpleNamespace(scalars=Mock())
        self.assertIsNone(update_risk_events(db, window, SimpleNamespace()))
        db.scalars.assert_not_called()

    def test_robust_z_uses_company_median(self):
        self.assertAlmostEqual(_robust_z(10.0, [1, 2, 2, 2, 3, 2, 2, 2]), 10.0)
        self.assertEqual(_robust_z(2.0, [1, 2, 2, 2, 3, 2, 2, 2]), 0.0)

    def test_group_split_never_leaks_a_story(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            {"group": f"g{group}", "time": start + timedelta(days=group), "row": row}
            for group in range(10)
            for row in range(2)
        ]
        splits = chronological_group_split(
            rows,
            group_key=lambda item: item["group"],
            time_key=lambda item: item["time"],
        )
        groups = {name: {item["group"] for item in items} for name, items in splits.items()}
        self.assertFalse(groups["train"] & groups["validation"])
        self.assertFalse(groups["train"] & groups["test"])
        self.assertFalse(groups["validation"] & groups["test"])

    @patch("app.services.klue_nli.get_klue_nli_classifier")
    def test_nli_can_enrich_keyword_risk_types(self, get_classifier):
        get_classifier.return_value.score_hypotheses.return_value = [[0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.60, 0.05, 0.05]]
        settings = Settings(
            _env_file=None,
            database_url="sqlite://",
            risk_type_nli_enabled=True,
        )
        base = classify_risk_types(["배송이 장기간 멈췄다"])
        enriched = enrich_risk_types_with_nli(["배송이 장기간 멈췄다"], base, settings)
        self.assertGreaterEqual(enriched["supply_operations"], 0.36)

    @patch("app.services.risk_analysis.classify_risk_types")
    @patch("app.services.risk_analysis.predict_risk_types")
    def test_promoted_risk_type_model_precedes_keyword_bootstrap(
        self,
        predict_risk_types,
        classify_risk_types_mock,
    ):
        predict_risk_types.return_value = {
            "version": "reviewed-types-v1",
            "scores": {"security_privacy": 0.91},
        }
        scores = resolve_risk_type_scores(
            ["검수 학습 모델이 개인정보 유출을 판정한다."],
            risk_keyword_count=1,
            negative_probability=0.8,
            settings=Settings(_env_file=None, database_url="sqlite://"),
        )
        self.assertEqual(scores["security_privacy"], 0.91)
        self.assertEqual(scores["product_quality"], 0.0)
        classify_risk_types_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
