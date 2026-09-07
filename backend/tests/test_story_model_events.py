"""PostgreSQL rollback-only checks for model-driven event reconciliation."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from sqlalchemy import func, select

from app.config import Settings
from app.database import SessionLocal
from app.models import (
    ArticleFilterResult, ArticleRiskAssessment, Company, CompanyArticleMatch, NewsArticle,
    RawNewsArticle, RiskEvent, RiskEventArticle, RiskEventLabel, RiskEventType,
    StoryCluster, StoryClusterArticle, StoryRiskScore,
)
from app.services.story_model_events import refresh_story_model_events


class StoryModelEventDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            self.company = self.db.scalar(select(Company).order_by(Company.id).limit(1))
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL test connection is unavailable: {exc}")
        if self.company is None:
            self.skipTest("An existing company is required for rollback-only integration tests")
        StoryRiskScore.__table__.create(bind=self.db.connection(), checkfirst=True)
        self.settings = Settings(story_event_min_articles=2, story_event_inactivity_days=3)
        self.start = datetime(2096, 1, 1, tzinfo=timezone.utc)
        self.now = self.start + timedelta(days=2)
        self.prefix = uuid.uuid4().hex
        self.cluster = StoryCluster(fingerprint=self.prefix, representative_title="개인정보 유출 사고",
                                    first_published_at=self.start, last_published_at=self.start)
        self.db.add(self.cluster)
        self.db.flush()
        self.probability = .8
        self.runtime = SimpleNamespace(available=True, reason=None, message="test", predict=self.predict)
        self.runtime_patch = patch("app.services.story_model_events.resolve_story_risk_runtime", return_value=self.runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def tearDown(self):
        if self.transaction.is_active:
            self.transaction.rollback()
        self.db.close()

    def predict(self, snapshots):
        return [dict(available=True, story_id=s["story_id"], company_id=s["company_id"],
                     risk_probability=self.probability, is_risk=self.probability >= .15,
                     anomaly_score=-.12, anomaly_percentile=.3, threshold=.15,
                     model_version="test-if-lgbm", model_state="provisional", artifact_sha256="a" * 64,
                     snapshot_hash=s["snapshot_hash"]) for s in snapshots]

    def add_article(self, index, *, accepted=True, assessment=True, hours=None, title="개인정보 유출 사고"):
        url = f"https://story-model-test.example/{self.prefix}/{index}"
        timestamp = self.start + timedelta(hours=index if hours is None else hours)
        raw = RawNewsArticle(source="test", title=title, url=url, normalized_url=url, content_hash=f"{index:064x}")
        self.db.add(raw)
        self.db.flush()
        article = NewsArticle(source="test", title=title, summary="후속 보도", url=url, raw_article_id=raw.id,
                              created_at=timestamp, published_at=timestamp, analyzed_at=timestamp, negative_probability=.1)
        self.db.add(article)
        self.db.flush()
        self.db.add(CompanyArticleMatch(company_id=self.company.id, article_id=article.id))
        self.db.add(StoryClusterArticle(article_id=article.id, story_cluster_id=self.cluster.id,
                                       similarity=.95, is_representative=index == 0))
        if accepted:
            self.db.add(ArticleFilterResult(company_id=self.company.id, raw_article_id=raw.id,
                                           curated_article_id=article.id, decision="accepted", reason="accepted",
                                           classifier_kind="test", filter_version="test", relevance_score=.95))
        if assessment:
            self.db.add(ArticleRiskAssessment(company_id=self.company.id, article_id=article.id,
                                             story_cluster_id=self.cluster.id, decision="non_risk", risk_probability=.1,
                                             type_scores={"security_privacy": .3}, primary_type=None, relevance_score=.95,
                                             source_domain="story-model-test.example", source_credibility=.65,
                                             classifier_kind="test", model_version="test", reason="non-risk test fixture"))
        self.db.flush()
        return article

    def refresh(self, **kwargs):
        return refresh_story_model_events(self.db, self.company.id, [self.cluster.id],
                                          settings=self.settings, as_of=self.now, **kwargs)

    def event(self):
        return self.db.scalar(select(RiskEvent).where(RiskEvent.event_key == f"story-v3:{self.company.id}:{self.cluster.id}"))

    def test_model_positive_opens_with_old_nonrisk_articles_and_own_if_score(self):
        first = self.add_article(0)
        second = self.add_article(1)
        result = self.refresh()
        event = self.event()
        self.assertEqual(result["events_created"], 1)
        self.assertEqual(result["risk_predictions"], 1)
        self.assertEqual(event.risk_probability, .8)
        self.assertEqual(event.anomaly_score, -.12)
        self.assertIsNone(event.feature_window_id)
        self.assertEqual(event.severity, "warning")
        self.assertEqual(event.opened_at, second.published_at)
        self.assertEqual(event.response_generation_status, "deferred")
        self.assertEqual(result["event_ids_to_enqueue"], [])
        self.assertEqual(set(self.db.scalars(select(RiskEventArticle.article_id).where(
            RiskEventArticle.risk_event_id == event.id))), {first.id, second.id})
        score = self.db.get(StoryRiskScore, (self.company.id, self.cluster.id))
        self.assertEqual(score.artifact_sha256, "a" * 64)
        self.assertEqual(score.article_count, 2)
        self.assertEqual(score.threshold, .15)

    def test_repeat_is_idempotent_and_never_queues_backfill_drafts(self):
        self.add_article(0)
        self.add_article(1)
        self.refresh()
        event = self.event()
        previous = (event.id, event.evidence_revision, event.response_generation_status, event.status)
        result = self.refresh(enqueue_drafts=True)
        self.assertEqual(result["events_changed"], 0)
        self.assertEqual(result["events_created"], 0)
        self.assertEqual(result["event_ids_to_enqueue"], [])
        self.assertEqual(previous, (event.id, event.evidence_revision, event.response_generation_status, event.status))

    def test_closed_inactive_repeat_does_not_reopen_or_change_evidence_revision(self):
        self.add_article(0)
        self.add_article(1)
        self.refresh()
        event = self.event()
        event.status = "closed"
        event.closed_at = self.start + timedelta(days=4)
        event.closure_reason = "no_related_articles_3_days"
        event.response_generation_status = "idle"
        event.last_response_revision = event.evidence_revision
        self.now = self.start + timedelta(days=10)
        self.db.flush()
        revision = event.evidence_revision
        result = self.refresh(enqueue_drafts=True)
        self.assertEqual(event.status, "closed")
        self.assertEqual(event.evidence_revision, revision)
        self.assertEqual(result["events_changed"], 0)
        self.assertEqual(result["event_ids_to_enqueue"], [])

    def test_negative_withdraws_auto_event_but_preserves_history(self):
        self.add_article(0)
        self.add_article(1)
        self.refresh()
        event = self.event()
        self.probability = .05
        result = self.refresh()
        self.assertEqual(result["events_withdrawn"], 1)
        self.assertEqual(event.status, "legacy_candidate")
        self.assertEqual(event.closure_reason, "story_model_non_risk")
        self.assertEqual(event.risk_probability, .05)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RiskEventArticle).where(
            RiskEventArticle.risk_event_id == event.id)), 2)
        self.assertFalse(self.db.get(StoryRiskScore, (self.company.id, self.cluster.id)).is_risk)
        self.assertEqual(self.refresh()["events_changed"], 0)

    def test_confirmed_human_event_is_preserved_while_model_score_updates(self):
        self.add_article(0)
        self.add_article(1)
        self.refresh()
        event = self.event()
        self.db.add(RiskEventLabel(risk_event_id=event.id, annotator="runtime-test", is_risk=True,
                                  event_start=self.start, status="confirmed", risk_types=["security_privacy"]))
        self.db.flush()
        self.probability = .01
        result = self.refresh()
        self.assertEqual(result["protected_events"], 1)
        self.assertEqual(event.status, "open")
        self.assertEqual(event.risk_probability, .8)
        self.assertEqual(self.db.get(StoryRiskScore, (self.company.id, self.cluster.id)).risk_probability, .01)

    def test_minimum_uses_accepted_current_articles_beyond_frozen_window(self):
        self.add_article(0)
        self.add_article(1, accepted=False)
        result = self.refresh()
        self.assertEqual(result["risk_predictions"], 1)
        self.assertIsNone(self.event())
        self.add_article(2, hours=30, assessment=False)
        result = self.refresh()
        self.assertEqual(result["events_created"], 1)
        self.assertEqual(self.db.get(StoryRiskScore, (self.company.id, self.cluster.id)).article_count, 1)
        self.assertEqual(self.event().last_evidence_at, self.start + timedelta(hours=30))

    def test_unavailable_and_failed_batch_make_no_score_or_event_writes(self):
        self.add_article(0)
        self.add_article(1)
        self.runtime.available = False
        self.runtime.reason = "artifact_missing"
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.refresh()
        self.assertIsNone(self.event())
        self.assertIsNone(self.db.get(StoryRiskScore, (self.company.id, self.cluster.id)))
        self.runtime.available = True
        self.runtime.predict = lambda rows: [dict(available=False, message="invalid features")]
        with self.assertRaisesRegex(RuntimeError, "before persistence"):
            self.refresh()
        self.assertIsNone(self.event())
        self.assertIsNone(self.db.get(StoryRiskScore, (self.company.id, self.cluster.id)))

    def test_unclassified_positive_uses_no_invented_type_or_article_probability(self):
        self.add_article(0, assessment=False, title="기업 새소식")
        self.add_article(1, assessment=False, title="기업 새소식 후속")
        self.refresh()
        event = self.event()
        self.assertIsNone(event.primary_type)
        self.assertEqual(list(self.db.scalars(select(RiskEventType).where(RiskEventType.risk_event_id == event.id))), [])
        probabilities = list(self.db.scalars(select(RiskEventArticle.risk_probability).where(
            RiskEventArticle.risk_event_id == event.id)))
        self.assertEqual(probabilities, [0, 0])

    def test_no_longer_accepted_story_withdraws_previous_event(self):
        first = self.add_article(0)
        second = self.add_article(1)
        self.refresh()
        for result in self.db.scalars(select(ArticleFilterResult).where(
                ArticleFilterResult.company_id == self.company.id,
                ArticleFilterResult.curated_article_id.in_([first.id, second.id]))):
            result.decision = "rejected"
            result.reason = "irrelevant"
        self.db.flush()
        result = self.refresh()
        self.assertEqual(result["scored"], 0)
        self.assertEqual(result["events_withdrawn"], 1)
        self.assertEqual(self.event().closure_reason, "story_model_insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
