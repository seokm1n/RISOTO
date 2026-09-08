"""Notification eligibility follows current judgments and Seoul article dates."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import (
    ArticleFilterResult, Company, CompanyArticleMatch, NewsArticle, RawNewsArticle,
    RiskEvent, RiskEventArticle, StoryCluster, StoryClusterArticle, StoryRiskScore, User,
)
from app.routers.notifications import _notification_period, list_notifications
from app.services.period_aggregation import SEOUL
from tests.auth_helpers import auth_for_company


class NotificationPeriodTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.start = datetime(2026, 9, 5, 15, tzinfo=timezone.utc)
        self.end = self.start + timedelta(days=3)
        self.runtime = SimpleNamespace(available=True, version="notification-v2")
        for target, value in (
            ("get_settings", Settings(story_risk_engine_enabled=True, story_risk_model_enabled=True)),
            ("resolve_story_risk_runtime", self.runtime),
            ("_notification_period", (self.start, self.end)),
        ):
            mock = patch(f"app.routers.notifications.{target}", return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.db.add(User(id=1, email="notification-period@example.test", password_hash="test"))
        self.db.add(Company(
            id=1, user_id=1, name="테스트", normalized_name="테스트",
            company_role="competitor", annual_revenue_krw=1000000000,
            company_size_class="small_medium", monitoring_status="active", analysis_status="ready",
        ))
        self.db.flush()
        self.auth = auth_for_company(self.db, 1)

    def issue(self, issue_id, latest_at, *, risk=True, score_version="notification-v2",
              event_version="notification-v2", scored=True, status="open", article_count=2):
        self.db.add(StoryCluster(
            id=issue_id, fingerprint=f"notification-{issue_id}", representative_title="위험 이슈",
            first_published_at=latest_at, last_published_at=latest_at,
        ))
        event = RiskEvent(
            id=issue_id, company_id=1, story_cluster_id=issue_id,
            event_source="story_v2", model_version=event_version,
            risk_probability=0.9, anomaly_score=0.2, severity="warning", status=status,
            opened_at=self.start - timedelta(days=30), last_seen_at=self.end,
        )
        self.db.add(event)
        self.db.flush()
        for index in range(article_count):
            article_id = issue_id * 10 + index
            url = f"https://notification.example.test/{article_id}"
            self.db.add(NewsArticle(
                id=article_id, source="test", title=f"관련 기사 {article_id}", url=url,
                published_at=latest_at - timedelta(minutes=index),
            ))
            self.db.add(RawNewsArticle(
                id=article_id, source="test", title="관련 기사", url=url,
                normalized_url=url, content_hash=f"notification-{article_id}",
            ))
            self.db.flush()
            self.db.add_all([
                ArticleFilterResult(
                    id=article_id, company_id=1, raw_article_id=article_id,
                    curated_article_id=article_id, decision="accepted", reason="accepted",
                    classifier_kind="test", filter_version="test", relevance_score=0.9,
                ),
                CompanyArticleMatch(company_id=1, article_id=article_id, matched_keyword="테스트"),
                StoryClusterArticle(article_id=article_id, story_cluster_id=issue_id,
                                    similarity=1.0, is_representative=index == 0),
                RiskEventArticle(risk_event_id=issue_id, article_id=article_id, evidence_score=0.8),
            ])
        if scored:
            self.db.add(StoryRiskScore(
                company_id=1, story_cluster_id=issue_id, risk_probability=0.9 if risk else 0.007,
                is_risk=risk, anomaly_score=0.2, anomaly_percentile=0.8,
                threshold=0.15, model_version=score_version, model_state="provisional",
                artifact_sha256="a" * 64, snapshot_hash="b" * 64, as_of=self.start,
                article_count=article_count, input_snapshot={},
            ))
        self.db.flush()
        return event

    def response(self):
        self.db.flush()
        return list_notifications(self.db, self.auth)

    def test_three_seoul_calendar_dates(self):
        # The imported function remains callable despite the router's period patch.
        with patch("app.routers.notifications.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 8, 0, 1, tzinfo=SEOUL)
            self.assertEqual(_notification_period(), (self.start, self.end))

    def test_latest_article_boundary_and_expiry_ignore_event_activity(self):
        self.issue(1, self.start)
        self.issue(2, self.start - timedelta(microseconds=1))
        self.issue(3, self.end - timedelta(microseconds=1))
        self.issue(4, self.end)
        response = self.response()
        self.assertEqual({item.risk_event_id for item in response.items}, {1, 3})
        self.assertEqual(response.total, 2)
        self.assertEqual(response.unread_count, 2)
        first = next(item for item in response.items if item.risk_event_id == 1)
        self.assertEqual(first.latest_article_at.replace(tzinfo=timezone.utc), self.start)
        self.assertLess(first.created_at.replace(tzinfo=timezone.utc), self.start)
        with patch("app.routers.notifications._notification_period", return_value=(self.end, self.end + timedelta(days=3))):
            self.assertEqual({item.risk_event_id for item in self.response().items}, {4})

    def test_current_nonrisk_missing_or_outdated_judgments_do_not_alert(self):
        self.issue(1, self.start, risk=False, event_version="old-hybrid")
        self.issue(2, self.start, risk=False)
        self.issue(3, self.start, scored=False)
        self.issue(4, self.start, score_version="old-model")
        self.issue(5, self.start, event_version="old-model")
        self.issue(6, self.start)
        self.assertEqual([item.risk_event_id for item in self.response().items], [6])
        self.runtime.available = False
        self.assertEqual(self.response().total, 0)

    def test_latest_rejected_article_cannot_refresh_or_qualify_issue(self):
        self.issue(1, self.start, article_count=3)
        self.issue(2, self.start)
        for issue_id in (1, 2):
            article_id = issue_id * 10
            self.db.add(ArticleFilterResult(
                id=1000 + issue_id, company_id=1, raw_article_id=article_id,
                curated_article_id=article_id, decision="rejected", reason="irrelevant",
                classifier_kind="test", filter_version="test-v2", relevance_score=0.1,
            ))
        # Issue 1 has two older accepted articles; issue 2 has only one accepted article.
        self.assertEqual(self.response().total, 0)

    def test_deduplicates_issue_and_excludes_inactive_or_singleton_events(self):
        self.issue(1, self.start)
        self.issue(2, self.start, status="closed")
        self.issue(3, self.start, status="acknowledged")
        self.issue(4, self.start, article_count=1)
        self.issue(5, self.start, status="monitoring")
        self.db.add(RiskEvent(
            id=100, company_id=1, story_cluster_id=1, event_source="story_v2",
            model_version="notification-v2", risk_probability=0.9, anomaly_score=0.2,
            severity="warning", status="open", opened_at=self.start, last_seen_at=self.start,
        ))
        self.db.flush()
        for article_id in (10, 11):
            self.db.add(RiskEventArticle(risk_event_id=100, article_id=article_id, evidence_score=0.8))
        self.assertEqual({item.risk_event_id for item in self.response().items}, {100, 5})


if __name__ == "__main__":
    unittest.main()
