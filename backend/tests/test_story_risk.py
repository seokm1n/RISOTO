"""기사별 위험 판정과 사건 개방 기준의 결정적 계약."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal
from app.models import (
    ArticleRiskAssessment,
    Company,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
    StoryCluster,
    StoryClusterArticle,
)
from app.presenters import risk_event_read
from app.services.story_risk import (
    _aggregate_story_event,
    _local_assessment,
    _local_assessment_batch,
    _reconcile_story_event_lifecycle,
    _story_event_inactivity_cutoff,
    meets_event_threshold,
    source_credibility,
    source_domain,
)


class StoryRiskTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            _env_file=None,
            database_url="sqlite://",
            article_risk_candidate_threshold=0.65,
            article_risk_high_threshold=0.80,
            article_risk_uncertain_low=0.35,
            story_event_min_articles=2,
        )

    def test_source_domain_uses_original_publisher_url(self):
        self.assertEqual(source_domain("https://www.example.com/news/1"), "example.com")
        self.assertEqual(source_credibility("privacy.go.kr"), 0.95)
        self.assertLess(source_credibility("youtube.com"), source_credibility("example.com"))

    def test_high_risk_single_article_does_not_open_event(self):
        self.assertFalse(meets_event_threshold([0.99], 1, self.settings))

    def test_two_story_articles_open_event_with_one_risk_candidate(self):
        self.assertTrue(meets_event_threshold([0.81], 2, self.settings))
        self.assertFalse(meets_event_threshold([], 2, self.settings))

    def test_inactivity_cutoff_waits_for_three_complete_seoul_dates(self):
        # 2026-09-07 00:00 KST: September 4, 5 and 6 are now complete.
        now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(
            _story_event_inactivity_cutoff(now, 3),
            datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc),
        )

    @patch("app.services.story_risk.resolve_risk_type_scores")
    def test_local_assessment_marks_clear_high_signal_as_risk(self, resolve_scores):
        resolve_scores.return_value = {
            "security_privacy": 0.95,
            "product_quality": 0.0,
            "safety_accident": 0.0,
            "legal_regulatory": 0.0,
            "labor_hr": 0.0,
            "financial_governance": 0.0,
            "supply_operations": 0.0,
            "reputation_consumer": 0.0,
        }
        article = SimpleNamespace(
            title="고객 개인정보 대규모 유출 사고",
            summary="해킹 피해가 확인됐다.",
            negative_probability=0.9,
        )
        result = _local_assessment(article, 0.95, self.settings)
        self.assertEqual(result["decision"], "risk")
        self.assertEqual(result["primary_type"], "security_privacy")
        self.assertGreaterEqual(result["risk_probability"], 0.80)

    @patch("app.services.story_risk.resolve_article_risk_type_scores_batch")
    def test_local_assessment_batch_preserves_article_decisions(self, resolve_scores):
        resolve_scores.return_value = [{
            "security_privacy": 0.95,
            "product_quality": 0.0,
            "safety_accident": 0.0,
            "legal_regulatory": 0.0,
            "labor_hr": 0.0,
            "financial_governance": 0.0,
            "supply_operations": 0.0,
            "reputation_consumer": 0.0,
        }]
        article = SimpleNamespace(
            id=101,
            title="고객 개인정보 유출 사고",
            summary="해킹 피해가 확인됐다.",
            negative_probability=0.9,
        )

        result = _local_assessment_batch([(article, 0.95)], self.settings)

        self.assertEqual(result[101]["decision"], "risk")
        self.assertEqual(result[101]["primary_type"], "security_privacy")


class StoryRiskDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            self.company_id = self.db.scalar(select(Company.id).order_by(Company.id).limit(1))
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.company_id is None:
            self.skipTest("사건 집계 테스트에 기업 한 곳이 필요합니다.")
        self.settings = Settings(
            article_risk_candidate_threshold=0.65,
            article_risk_high_threshold=0.80,
            story_event_min_articles=2,
        )

    def tearDown(self):
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        self.db.close()

    def test_high_risk_article_creates_story_event_with_ranked_evidence(self):
        timestamp = datetime(2098, 1, 1, tzinfo=timezone.utc)
        cluster = StoryCluster(
            fingerprint="story-risk-test-high-single",
            representative_title="개인정보 유출 사고",
            first_published_at=timestamp,
            last_published_at=timestamp,
        )
        self.db.add(cluster)
        self.db.flush()
        article = NewsArticle(
            source="test",
            title="개인정보 유출 사고",
            url="https://story-risk-test.example/security-incident",
            published_at=timestamp,
            negative_probability=0.95,
        )
        self.db.add(article)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=article.id,
                story_cluster_id=cluster.id,
                similarity=1.0,
                is_representative=True,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id,
                article_id=article.id,
                story_cluster_id=cluster.id,
                decision="risk",
                risk_probability=0.91,
                type_scores={"security_privacy": 0.95},
                primary_type="security_privacy",
                relevance_score=0.95,
                source_domain="story-risk-test.example",
                source_credibility=0.65,
                classifier_kind="test",
                model_version="test",
                reason="test fixture",
            )
        )
        related_article = NewsArticle(
            source="naver_api_hub",
            title="당국, 개인정보 사고 후속 조사",
            url="https://portal.example/redirect/related",
            original_url="https://story-risk-test.example/security-followup",
            published_at=timestamp + timedelta(hours=1),
            negative_probability=0.20,
        )
        self.db.add(related_article)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=related_article.id,
                story_cluster_id=cluster.id,
                similarity=0.82,
                is_representative=False,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id,
                article_id=related_article.id,
                story_cluster_id=cluster.id,
                decision="non_risk",
                risk_probability=0.40,
                type_scores={"security_privacy": 0.30},
                primary_type=None,
                relevance_score=0.95,
                source_domain="story-risk-test.example",
                source_credibility=0.65,
                classifier_kind="test",
                model_version="test",
                reason="same-story follow-up below risk cutoff",
            )
        )
        second_related_article = NewsArticle(
            source="tavily",
            title="개인정보 사고 추가 후속 보도",
            url="https://story-risk-test.example/security-second-followup",
            published_at=timestamp + timedelta(hours=2),
            negative_probability=0.25,
        )
        self.db.add(second_related_article)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=second_related_article.id,
                story_cluster_id=cluster.id,
                similarity=0.80,
                is_representative=False,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id,
                article_id=second_related_article.id,
                story_cluster_id=cluster.id,
                decision="non_risk",
                risk_probability=0.42,
                type_scores={"security_privacy": 0.32},
                primary_type=None,
                relevance_score=0.95,
                source_domain="story-risk-test.example",
                source_credibility=0.65,
                classifier_kind="test",
                model_version="test",
                reason="second same-publisher follow-up below risk cutoff",
            )
        )
        self.db.flush()

        event_id, should_generate = _aggregate_story_event(
            self.db,
            self.company_id,
            cluster.id,
            "security_privacy",
            self.settings,
        )

        self.assertTrue(should_generate)
        event = self.db.get(RiskEvent, event_id)
        self.assertEqual(event.event_source, "story_v2")
        self.assertEqual(event.story_cluster_id, cluster.id)
        self.assertEqual(event.opened_at, timestamp + timedelta(hours=1))
        self.assertEqual(event.response_generation_status, "pending")
        evidence = self.db.get(RiskEventArticle, (event_id, article.id))
        self.assertGreater(evidence.evidence_score, 0.8)
        self.assertIsNotNone(
            self.db.get(RiskEventArticle, (event_id, related_article.id))
        )
        projected = risk_event_read(self.db, event)
        self.assertEqual(projected.evidence_article_count, 3)
        self.assertEqual(projected.source_count, 1)
        self.assertEqual(projected.risk_article_count, 1)
        self.assertEqual(projected.risk_source_count, 1)
        self.assertEqual(
            {item["article_id"]: item["evidence_role"] for item in projected.evidence_articles},
            {
                article.id: "trigger",
                related_article.id: "context",
                second_related_article.id: "context",
            },
        )

        # A historical re-filter can remove one article from the company while
        # leaving the story itself valid.  Re-aggregation must prune that old
        # evidence instead of retaining a stale article link forever.
        self.db.delete(
            self.db.get(
                ArticleRiskAssessment,
                (self.company_id, related_article.id),
            )
        )
        self.db.flush()
        _aggregate_story_event(
            self.db,
            self.company_id,
            cluster.id,
            "security_privacy",
            self.settings,
        )
        self.assertIsNone(
            self.db.get(RiskEventArticle, (event_id, related_article.id))
        )
        self.db.refresh(event)
        self.assertEqual(event.opened_at, timestamp + timedelta(hours=2))

        # Falling below the opening threshold can prune evidence, but it must not
        # close an already opened story based on risk probability or article count.
        self.db.delete(
            self.db.get(
                ArticleRiskAssessment,
                (self.company_id, second_related_article.id),
            )
        )
        self.db.flush()
        closed_event_id, should_generate = _aggregate_story_event(
            self.db,
            self.company_id,
            cluster.id,
            "security_privacy",
            self.settings,
        )
        self.assertEqual(closed_event_id, event_id)
        self.assertFalse(should_generate)
        self.db.refresh(event)
        self.assertEqual(event.status, "monitoring")
        self.assertIsNone(event.closed_at)
        self.assertEqual(event.risk_probability, 0.91)
        self.assertEqual(event.article_id, article.id)
        self.assertIsNotNone(self.db.get(RiskEventArticle, (event_id, article.id)))
        self.assertIsNotNone(
            self.db.get(RiskEventType, (event_id, "security_privacy"))
        )

        # It closes only after three complete Seoul dates without another article.
        result = _reconcile_story_event_lifecycle(
            self.db,
            self.settings,
            company_id=self.company_id,
            now=datetime(2098, 1, 4, 15, 0, tzinfo=timezone.utc),
        )
        self.db.refresh(event)
        self.assertGreaterEqual(result["closed"], 1)
        self.assertEqual(event.status, "closed")
        self.assertEqual(
            event.closed_at,
            datetime(2098, 1, 4, 15, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(event.closure_reason, "no_related_articles_3_days")

    @patch("app.services.story_risk.WINDOW_SIGNAL_BLEND_WEIGHT", 0.2)
    @patch("app.services.story_risk.resolve_production_risk_detector")
    def test_window_signal_blends_into_story_probability(self, resolve_detector):
        """블렌드를 켰을 때(w=0.2) window_v1 신호가 percentile로 정규화돼 섞이는지 검증.

        2026-09-07 현재 기본 가중치는 0.0(꺼짐)이다 -- story_v2 자체 라벨 117건으로
        재검증하니 블렌드가 오히려 정확도를 낮췄기 때문(AUC 0.8343 -> 0.8196). 이
        테스트는 나중에 근거를 다시 만들어 가중치를 올릴 때 메커니즘 자체가 여전히
        맞는지 보려고 값을 강제로 켜서 검증한다.
        """
        from app.models import CompanyFeatureWindow
        from app.services.story_risk import _clamp

        WINDOW_SIGNAL_BLEND_WEIGHT = 0.2
        timestamp = datetime(2098, 1, 2, tzinfo=timezone.utc)
        cluster = StoryCluster(
            fingerprint="story-risk-test-window-blend",
            representative_title="공급망 이상 신호",
            first_published_at=timestamp,
            last_published_at=timestamp,
        )
        self.db.add(cluster)
        self.db.flush()
        window = CompanyFeatureWindow(
            company_id=self.company_id,
            window_start=timestamp - timedelta(minutes=15),
            window_end=timestamp,
            data_quality="complete",
            risk_probability=0.42,
        )
        self.db.add(window)

        article = NewsArticle(
            source="test",
            title="공급망 이상 신호 최초 보도",
            url="https://story-risk-test.example/window-blend-primary",
            published_at=timestamp,
            negative_probability=0.7,
        )
        self.db.add(article)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=article.id, story_cluster_id=cluster.id,
                similarity=1.0, is_representative=True,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id, article_id=article.id, story_cluster_id=cluster.id,
                decision="risk", risk_probability=0.70, type_scores={"supply_operations": 0.7},
                primary_type="supply_operations", relevance_score=0.9,
                source_domain="story-risk-test.example", source_credibility=0.65,
                classifier_kind="test", model_version="test", reason="test fixture",
            )
        )
        follow_up = NewsArticle(
            source="test", title="공급망 이상 후속 보도",
            url="https://story-risk-test.example/window-blend-followup",
            published_at=timestamp + timedelta(minutes=5), negative_probability=0.5,
        )
        self.db.add(follow_up)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=follow_up.id, story_cluster_id=cluster.id,
                similarity=0.9, is_representative=False,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id, article_id=follow_up.id, story_cluster_id=cluster.id,
                decision="non_risk", risk_probability=0.30, type_scores={"supply_operations": 0.3},
                primary_type=None, relevance_score=0.9,
                source_domain="story-risk-test.example", source_credibility=0.65,
                classifier_kind="test", model_version="test", reason="test fixture",
            )
        )
        self.db.flush()

        resolve_detector.return_value = SimpleNamespace(
            available=True,
            payload={"reference_probabilities": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]},
        )

        event_id, _ = _aggregate_story_event(
            self.db, self.company_id, cluster.id, "supply_operations", self.settings,
        )

        event = self.db.get(RiskEvent, event_id)
        # window_percentile = mean(reference <= 0.42) = 0.4 (four of ten reference
        # values, 0.1-0.4, are <= 0.42).
        window_percentile = 0.4
        base_probability = 0.70  # the max per-article risk_probability among candidates
        expected = _clamp(
            (1 - WINDOW_SIGNAL_BLEND_WEIGHT) * base_probability
            + WINDOW_SIGNAL_BLEND_WEIGHT * window_percentile
        )
        self.assertAlmostEqual(event.risk_probability, expected, places=6)
        self.assertNotEqual(event.risk_probability, base_probability)

    @patch("app.services.story_risk.resolve_production_risk_detector")
    def test_window_signal_is_a_noop_at_the_current_zero_weight(self, resolve_detector):
        """현재 기본값(w=0.0)에서는 window 신호를 아예 조회하지 않는다."""
        from app.models import CompanyFeatureWindow

        timestamp = datetime(2098, 1, 3, tzinfo=timezone.utc)
        cluster = StoryCluster(
            fingerprint="story-risk-test-window-blend-off",
            representative_title="공급망 이상 신호 2",
            first_published_at=timestamp,
            last_published_at=timestamp,
        )
        self.db.add(cluster)
        self.db.flush()
        self.db.add(
            CompanyFeatureWindow(
                company_id=self.company_id,
                window_start=timestamp - timedelta(minutes=15),
                window_end=timestamp,
                data_quality="complete",
                risk_probability=0.99,  # would drag the blend way up if it were used
            )
        )
        article = NewsArticle(
            source="test", title="공급망 이상 신호 2 최초 보도",
            url="https://story-risk-test.example/window-blend-off-primary",
            published_at=timestamp, negative_probability=0.7,
        )
        self.db.add(article)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=article.id, story_cluster_id=cluster.id,
                similarity=1.0, is_representative=True,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id, article_id=article.id, story_cluster_id=cluster.id,
                decision="risk", risk_probability=0.70, type_scores={"supply_operations": 0.7},
                primary_type="supply_operations", relevance_score=0.9,
                source_domain="story-risk-test.example", source_credibility=0.65,
                classifier_kind="test", model_version="test", reason="test fixture",
            )
        )
        follow_up = NewsArticle(
            source="test", title="공급망 이상 신호 2 후속 보도",
            url="https://story-risk-test.example/window-blend-off-followup",
            published_at=timestamp + timedelta(minutes=5), negative_probability=0.5,
        )
        self.db.add(follow_up)
        self.db.flush()
        self.db.add(
            StoryClusterArticle(
                article_id=follow_up.id, story_cluster_id=cluster.id,
                similarity=0.9, is_representative=False,
            )
        )
        self.db.add(
            ArticleRiskAssessment(
                company_id=self.company_id, article_id=follow_up.id, story_cluster_id=cluster.id,
                decision="non_risk", risk_probability=0.30, type_scores={"supply_operations": 0.3},
                primary_type=None, relevance_score=0.9,
                source_domain="story-risk-test.example", source_credibility=0.65,
                classifier_kind="test", model_version="test", reason="test fixture",
            )
        )
        self.db.flush()

        event_id, _ = _aggregate_story_event(
            self.db, self.company_id, cluster.id, "supply_operations", self.settings,
        )

        event = self.db.get(RiskEvent, event_id)
        self.assertEqual(event.risk_probability, 0.70)
        resolve_detector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
