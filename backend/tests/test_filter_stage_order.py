"""Isolated stage-order tests: no real models, external database or downloads."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.article_filtering import FilterConfig, FilterDecision, classify_article
from app.services.fine_tuned_text import predict_advertising
from app.services.monitoring_pipeline import _classify_company_article


class FilterStageOrderTests(unittest.TestCase):
    def setUp(self):
        self.company = SimpleNamespace(id=7, name="쿠팡", normalized_name="쿠팡", ticker=None)
        self.article = SimpleNamespace(id=20, title="쿠팡 물류센터 사고", summary="당국이 원인을 조사했다", url="https://news.example/a", source="naver")
        self.config = FilterConfig(ai_enabled=True, allow_model_download=False)
        self.calls = []
        self.ad = self.mock("predict_advertising", side_effect=self.advertising)
        self.reranker = self.mock("predict_company_relevance", side_effect=self.relevance)
        self.mock("predict_filter", return_value=None)
        self.mock("predict_topical_relevance", return_value=None)
        self.mock("get_klue_nli_classifier", return_value=None)

    def mock(self, name, **kwargs):
        p = patch(f"app.services.article_filtering.{name}", **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def advertising(self, *args):
        self.calls.append("advertising")
        return {"version": "spam-v2", "normal": .9, "advertising": .1, "input_schema": "company-title-content-v1"}

    def relevance(self, *args):
        self.calls.append("relevance")
        return {"version": "reranker", "relevant": .99, "reject_threshold": .3, "accept_threshold": .95}

    def classify(self, **kwargs):
        return classify_article(self.company, [], self.article, self.article, config=self.config, **kwargs)

    def test_duplicate_skips_all_models(self):
        canonical = SimpleNamespace(id=10, url=self.article.url)
        result = self.classify(candidate_articles=[canonical])
        self.assertEqual(result.reason, "duplicate")
        self.assertEqual(result.duplicate_of_raw_id, 10)
        self.ad.assert_not_called()
        self.reranker.assert_not_called()

    def test_ads_and_uncertain_ads_never_reach_relevance(self):
        for score, decision in [(.85, "rejected"), (.55, "review_required")]:
            with self.subTest(score=score):
                self.ad.side_effect = None
                self.ad.return_value = {"version": "spam-v2", "advertising": score}
                result = self.classify()
                self.assertEqual((result.decision, result.reason), (decision, "advertisement"))
                self.assertEqual(result.advertising_score, score)
                self.assertEqual(result.relevance_score, 0)
                self.reranker.assert_not_called()

    def test_advertising_precedes_relevance_and_scores_are_independent(self):
        result = self.classify()
        self.assertEqual(self.calls, ["advertising", "relevance"])
        self.assertEqual(result.decision, "accepted")
        self.assertEqual(result.advertising_score, .1)
        self.assertEqual(result.details["advertising_model_version"], "spam-v2")
        self.assertEqual(result.details["company_reranker_version"], "reranker")

    def test_missing_ad_model_records_rules_fallback(self):
        self.ad.side_effect = None
        self.ad.return_value = None
        result = self.classify()
        self.assertEqual(result.details["advertising_fallback"], "rules_only")
        self.reranker.assert_called_once()

    def test_duplicate_reuses_same_company_result(self):
        canonical = SimpleNamespace(id=10, url=self.article.url)
        previous = FilterDecision("accepted", "accepted", .95, .1, .95, "test", self.config.version)
        db = SimpleNamespace(scalar=Mock(return_value=previous))
        result = _classify_company_article(db, self.company, [], self.article, self.article,
            candidate_articles=[canonical], semantic_scorer=None, config=self.config)
        self.assertEqual(result.relevance_score, .95)
        self.assertTrue(result.details["canonical_filter_reused"])
        self.ad.assert_not_called()
        self.reranker.assert_not_called()
        values = db.scalar.call_args.args[0].compile().params.values()
        self.assertIn(self.company.id, values)
        self.assertIn(self.config.version, values)

    def test_duplicate_new_company_still_requires_its_own_judgment(self):
        canonical = SimpleNamespace(**{**vars(self.article), "id": 10})
        db = SimpleNamespace(scalar=Mock(return_value=None), get=Mock(return_value=canonical))
        self.ad.side_effect = None
        self.ad.return_value = {"version": "spam-v2", "advertising": .96}
        result = _classify_company_article(db, self.company, [], self.article, self.article,
            candidate_articles=[canonical], semantic_scorer=None, config=self.config)
        self.assertEqual(result.relevance_score, 0)
        self.assertEqual(result.advertising_score, .96)
        self.assertNotEqual(result.decision, "accepted")
        self.reranker.assert_not_called()

    @patch("app.services.fine_tuned_text.predict_relevance_batch")
    def test_ad_adapter_maps_filter_probability(self, predict):
        predict.return_value = [{"version": "spam", "relevant": .03, "irrelevant": .97, "input_schema": "company-title-content-v1"}]
        self.assertEqual(predict_advertising("쿠팡", "기사")["advertising"], .97)


if __name__ == "__main__":
    unittest.main()
