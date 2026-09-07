"""Story judgments display model scores without turning missing scores into negatives."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import (
    ArticleFilterResult, ArticleRiskAssessment, Company, CompanyArticleMatch, NewsArticle, RawNewsArticle,
    RiskEvent, RiskEventArticle, StoryCluster, StoryClusterArticle,
    StoryRiskScore, User,
)
from app.routers.collection import get_monitoring_summary, list_risk_judgments_page
from app.routers.companies import _to_response
from app.routers.dashboard import _reportable_risk_filters
from tests.auth_helpers import auth_for_company


class StoryModelListingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.now = datetime.now(timezone.utc)
        self.settings = Settings(story_risk_engine_enabled=True, story_risk_model_enabled=True)
        self.runtime = SimpleNamespace(available=True, model_state="provisional", version="story-test-v2")
        runtime_patch = patch("app.routers.collection.resolve_story_risk_runtime", return_value=self.runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.db.add(User(id=1, email="story-list@example.test", password_hash="test-only"))
        self.company = Company(
            id=1, user_id=1, name="목록 테스트", normalized_name="목록테스트",
            company_role="competitor", annual_revenue_krw=1_000_000_000,
            company_size_class="small_medium", monitoring_status="active", analysis_status="ready",
        )
        self.db.add(self.company)
        self.db.flush()
        self.auth = auth_for_company(self.db, 1)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def story(self, story_id, *, article_count=2, scored=True):
        cluster = StoryCluster(
            id=story_id, fingerprint=f"story-list-{story_id}",
            representative_title=f"스토리 {story_id}",
            first_published_at=self.now, last_published_at=self.now,
        )
        self.db.add(cluster)
        articles = []
        for index in range(article_count):
            article = NewsArticle(
                id=story_id * 10 + index, source="story-list-test", title=f"기사 {story_id}-{index}",
                url=f"https://publisher.example.test/{story_id}/{index}",
                published_at=self.now + timedelta(minutes=index),
            )
            self.db.add(article)
            self.db.flush()
            articles.append(article)
            self.db.add_all([
                RawNewsArticle(
                    id=article.id, source=article.source, title=article.title, url=article.url,
                    normalized_url=article.url, content_hash=f"story-list-hash-{article.id}",
                ),
                ArticleFilterResult(
                    id=article.id, raw_article_id=article.id, company_id=1, curated_article_id=article.id,
                    decision="accepted", reason="accepted", classifier_kind="test",
                    filter_version="story-list-test", relevance_score=0.95,
                ),
                CompanyArticleMatch(company_id=1, article_id=article.id, matched_keyword="테스트"),
                StoryClusterArticle(
                    article_id=article.id, story_cluster_id=story_id,
                    similarity=1.0, is_representative=index == 0,
                ),
                ArticleRiskAssessment(
                    company_id=1, article_id=article.id, story_cluster_id=story_id,
                    decision="risk", risk_probability=0.96, type_scores={"legal_regulatory": 0.8},
                    relevance_score=0.95, classifier_kind="rules_nli", model_version="old-article-model",
                ),
            ])
        if scored:
            self.db.add(StoryRiskScore(
                company_id=1, story_cluster_id=story_id, risk_probability=0.07,
                is_risk=False, anomaly_score=-0.123, anomaly_percentile=0.3,
                threshold=0.4, model_version="story-test-v2", model_state="provisional",
                artifact_sha256="a" * 64, snapshot_hash="b" * 64,
                as_of=self.now, article_count=article_count, input_snapshot={},
            ))
        self.db.flush()
        return cluster, articles

    def event(self, event_id, story, *, status="open", source="story_v2", evidence_count=2):
        cluster, articles = story
        event = RiskEvent(
            id=event_id, company_id=1, story_cluster_id=cluster.id if source == "story_v2" else None,
            event_key=f"story-list-event-{event_id}", event_source=source,
            risk_probability=0.9, anomaly_score=0.2, severity="warning", status=status,
            opened_at=self.now, last_seen_at=self.now,
        )
        self.db.add(event)
        self.db.flush()
        for article in articles[:evidence_count]:
            self.db.add(RiskEventArticle(risk_event_id=event_id, article_id=article.id, evidence_score=0.8))
        self.db.flush()
        return event

    def test_non_risk_uses_persisted_model_and_omits_unscored_and_single_article_stories(self):
        scored_story = self.story(1)
        self.story(2, scored=False)
        self.story(3, article_count=1)
        # Former ML-negative event remains auditable but does not count as risk.
        self.event(1, scored_story, status="legacy_candidate")
        with patch("app.routers.collection.get_settings", return_value=self.settings):
            result = list_risk_judgments_page(
                1, classification="non_risk", page=1, page_size=10, days=None, db=self.db, auth=self.auth,
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.summary.non_risk, 1)
        self.assertEqual(result.summary.risk, 0)
        item = result.items[0]
        self.assertEqual(item.story_cluster_id, 1)
        self.assertEqual(item.risk_probability, 0.07)
        self.assertEqual(item.anomaly_score, -0.123)
        self.assertEqual(item.model_version, "story-test-v2")
        self.assertEqual(item.model_state, "provisional")
        self.assertEqual(item.evidence_article_count, 2)

    def test_disabled_model_retains_article_aggregate_listing(self):
        self.story(1, scored=False)
        self.settings.story_risk_model_enabled = False
        with patch("app.routers.collection.get_settings", return_value=self.settings):
            result = list_risk_judgments_page(
                1, classification="non_risk", page=1, page_size=10, days=None, db=self.db, auth=self.auth,
            )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].risk_probability, 0.96)
        self.assertEqual(result.items[0].model_version, "old-article-model")

    def test_non_risk_eligibility_requires_current_accepted_membership_and_current_model(self):
        self.story(1)
        self.story(2)
        self.story(3)
        withdrawn = self.db.get(ArticleFilterResult, 20)
        withdrawn.decision = "rejected"
        withdrawn.reason = "irrelevant"
        old_score = self.db.get(StoryRiskScore, (1, 3))
        old_score.model_version = "previous-story-model"
        self.db.flush()
        with patch("app.routers.collection.get_settings", return_value=self.settings):
            result = list_risk_judgments_page(
                1, classification="non_risk", page=1, page_size=10, days=None, db=self.db, auth=self.auth,
            )
        self.assertEqual([item.story_cluster_id for item in result.items], [1])
        self.runtime.available = False
        with patch("app.routers.collection.get_settings", return_value=self.settings):
            historical = list_risk_judgments_page(
                1, classification="non_risk", page=1, page_size=10, days=None, db=self.db, auth=self.auth,
            )
        self.assertEqual({item.story_cluster_id for item in historical.items}, {1, 3})
        self.assertEqual(
            next(item for item in historical.items if item.story_cluster_id == 3).model_version,
            "previous-story-model",
        )

    def test_dashboard_excludes_window_events_insufficient_evidence_and_retracted_scores(self):
        story = self.story(1)
        self.event(1, story)
        self.event(2, story, status="closed")
        self.event(3, story, source="window_v1")
        self.event(4, story, evidence_count=1)
        self.event(5, story, status="legacy_candidate")
        self.event(6, story, status="dismissed")
        visible = self.db.scalars(select(RiskEvent.id).where(*_reportable_risk_filters(self.settings)))
        self.assertEqual(set(visible), {1, 2})
        self.settings.story_risk_engine_enabled = False
        old_visible = self.db.scalars(select(RiskEvent.id).where(*_reportable_risk_filters(self.settings)))
        self.assertEqual(set(old_visible), {1, 2, 3, 4})

    def test_company_and_monitoring_state_follow_story_runtime_without_window_model(self):
        runtime = SimpleNamespace(available=True, model_state="provisional", version="story-test-v2")
        with patch("app.routers.companies.get_settings", return_value=self.settings), patch(
            "app.routers.companies.resolve_story_risk_runtime", return_value=runtime,
        ), patch("app.routers.collection.resolve_story_risk_runtime", return_value=runtime):
            company = _to_response(self.db, self.company, None, [])
            monitoring = get_monitoring_summary(1, db=self.db, settings=self.settings, auth=self.auth)
        self.assertEqual(company.model_state, "provisional")
        self.assertEqual(monitoring.model_state, "provisional")
        self.assertEqual(company.model_version, "story-test-v2")
        self.assertEqual(monitoring.model_version, "story-test-v2")
        runtime.available = False
        with patch("app.routers.companies.get_settings", return_value=self.settings), patch(
            "app.routers.companies.resolve_story_risk_runtime", return_value=runtime,
        ):
            unavailable = _to_response(self.db, self.company, None, [])
        self.assertEqual(unavailable.model_state, "unavailable")
        self.assertIsNone(unavailable.model_version)


if __name__ == "__main__":
    unittest.main()
