"""기사별 위험 판정과 사건 개방 기준의 결정적 계약."""

from datetime import datetime, timezone
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
    StoryCluster,
    StoryClusterArticle,
)
from app.presenters import risk_event_read
from app.services.story_risk import (
    _aggregate_story_event,
    _local_assessment,
    _local_assessment_batch,
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
            story_event_min_distinct_sources=2,
        )

    def test_source_domain_uses_original_publisher_url(self):
        self.assertEqual(source_domain("https://www.example.com/news/1"), "example.com")
        self.assertEqual(source_credibility("privacy.go.kr"), 0.95)
        self.assertLess(source_credibility("youtube.com"), source_credibility("example.com"))

    def test_high_risk_single_article_opens_event(self):
        self.assertTrue(meets_event_threshold([0.81], ["one.example"], self.settings))

    def test_two_distinct_candidate_sources_open_event(self):
        self.assertTrue(
            meets_event_threshold(
                [0.66, 0.69],
                ["one.example", "two.example"],
                self.settings,
            )
        )
        self.assertFalse(
            meets_event_threshold(
                [0.66, 0.69],
                ["one.example", "one.example"],
                self.settings,
            )
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
            story_event_min_distinct_sources=2,
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
            original_url="https://second-publisher.example/security-followup",
            published_at=timestamp,
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
                source_domain="second-publisher.example",
                source_credibility=0.65,
                classifier_kind="test",
                model_version="test",
                reason="same-story follow-up below risk cutoff",
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
        self.assertEqual(event.response_generation_status, "pending")
        evidence = self.db.get(RiskEventArticle, (event_id, article.id))
        self.assertGreater(evidence.evidence_score, 0.8)
        self.assertIsNotNone(
            self.db.get(RiskEventArticle, (event_id, related_article.id))
        )
        projected = risk_event_read(self.db, event)
        self.assertEqual(projected.evidence_article_count, 2)
        self.assertEqual(projected.source_count, 2)
        self.assertEqual(projected.risk_article_count, 1)
        self.assertEqual(projected.risk_source_count, 1)
        self.assertEqual(
            {item["article_id"]: item["evidence_role"] for item in projected.evidence_articles},
            {article.id: "trigger", related_article.id: "context"},
        )


if __name__ == "__main__":
    unittest.main()
