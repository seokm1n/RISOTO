"""Operational risk views must hide human-dismissed false positives."""

from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Company,
    CompanyArticleMatch,
    CompanyDailySummary,
    CompanyFeatureWindow,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
)
from app.routers.collection import list_risk_events
from app.routers.dashboard import get_dashboard_overview
from app.services.risk_analysis import update_daily_summary
from tests.auth_helpers import auth_for_company


class RiskVisibilityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            self.company_id = self.db.scalar(
                select(Company.id).order_by(Company.id).limit(1)
            )
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.company_id is None:
            self.skipTest("위험 노출 테스트에 기업 한 곳이 필요합니다.")
        self.auth = auth_for_company(self.db, self.company_id)

    def tearDown(self):
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "db"):
            self.db.close()

    def test_dismissed_is_hidden_while_operational_and_closed_states_remain(self):
        baseline = get_dashboard_overview(days=90, db=self.db, auth=self.auth)
        baseline_company_count = next(
            item.risk_count
            for item in baseline.companies
            if item.id == self.company_id
        )
        start = datetime(2098, 1, 1, tzinfo=timezone.utc)
        statuses = (
            "open",
            "monitoring",
            "acknowledged",
            "closed",
            "dismissed",
            "legacy_candidate",
        )
        events: dict[str, RiskEvent] = {}
        for index, status in enumerate(statuses):
            detected_at = start + timedelta(minutes=index)
            event = RiskEvent(
                company_id=self.company_id,
                anomaly_score=0.8,
                risk_probability=0.9,
                severity="warning",
                status=status,
                summary=f"visibility-test-{status}",
                model_state="provisional",
                approval_state="draft",
                opened_at=detected_at,
                last_seen_at=detected_at,
                closed_at=detected_at if status == "closed" else None,
                detected_at=detected_at,
            )
            self.db.add(event)
            events[status] = event
        self.db.flush()

        normal_list = list_risk_events(
            self.company_id,
            limit=200,
            include_legacy=False,
            view="all",
            db=self.db,
            auth=self.auth,
        )
        legacy_list = list_risk_events(
            self.company_id,
            limit=200,
            include_legacy=True,
            view="all",
            db=self.db,
            auth=self.auth,
        )
        normal_ids = {item.id for item in normal_list}
        legacy_ids = {item.id for item in legacy_list}
        reportable = {"open", "monitoring", "acknowledged", "closed"}

        self.assertTrue({events[status].id for status in reportable} <= normal_ids)
        self.assertNotIn(events["dismissed"].id, normal_ids)
        self.assertNotIn(events["legacy_candidate"].id, normal_ids)
        self.assertNotIn(events["dismissed"].id, legacy_ids)
        self.assertIn(events["legacy_candidate"].id, legacy_ids)

        active_ids = {
            item.id
            for item in list_risk_events(
                self.company_id,
                limit=200,
                include_legacy=False,
                view="active",
                db=self.db,
                auth=self.auth,
            )
        }
        history_ids = {
            item.id
            for item in list_risk_events(
                self.company_id,
                limit=200,
                include_legacy=False,
                view="history",
                db=self.db,
                auth=self.auth,
            )
        }
        self.assertTrue({events[status].id for status in {"open", "monitoring", "acknowledged"}} <= active_ids)
        self.assertIn(events["closed"].id, history_ids)
        self.assertNotIn(events["closed"].id, active_ids)

        overview = get_dashboard_overview(days=90, db=self.db, auth=self.auth)
        overview_company_count = next(
            item.risk_count
            for item in overview.companies
            if item.id == self.company_id
        )
        recent_ids = {item.id for item in overview.recent_risks}
        self.assertEqual(overview.risk_count - baseline.risk_count, len(reportable))
        self.assertEqual(
            overview_company_count - baseline_company_count,
            len(reportable),
        )
        self.assertTrue({events[status].id for status in reportable} <= recent_ids)
        self.assertNotIn(events["dismissed"].id, recent_ids)
        self.assertNotIn(events["legacy_candidate"].id, recent_ids)

        update_daily_summary(self.db, self.company_id, start)
        self.db.flush()
        summary = self.db.scalar(
            select(CompanyDailySummary).where(
                CompanyDailySummary.company_id == self.company_id,
                CompanyDailySummary.summary_date
                == start.astimezone(ZoneInfo("Asia/Seoul")).date(),
            )
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary.risk_event_count, len(reportable))

    def test_daily_article_counts_share_the_valid_window_cohort(self):
        start = datetime(2098, 2, 1, tzinfo=timezone.utc)
        valid_window = CompanyFeatureWindow(
            company_id=self.company_id,
            window_start=start,
            window_end=start + timedelta(minutes=15),
            data_quality="complete",
            article_count=5,
            model_state="unavailable",
        )
        unavailable_window = CompanyFeatureWindow(
            company_id=self.company_id,
            window_start=start + timedelta(minutes=15),
            window_end=start + timedelta(minutes=30),
            data_quality="unavailable",
            article_count=1,
            model_state="unavailable",
        )
        self.db.add_all([valid_window, unavailable_window])

        labels = ("부정", "긍정", "neutral", "negative", None, "부정")
        articles = []
        for index, label in enumerate(labels):
            article = NewsArticle(
                source="daily-ratio-test",
                title=f"daily-ratio-test-{index}",
                url=f"https://daily-ratio.test/{self.company_id}/{index}",
                published_at=start + timedelta(minutes=3 * index if index < 5 else 18),
                sentiment_label=label,
            )
            self.db.add(article)
            self.db.flush()
            self.db.add(
                CompanyArticleMatch(
                    company_id=self.company_id,
                    article_id=article.id,
                )
            )
            articles.append(article)

        reportable = RiskEvent(
            company_id=self.company_id,
            anomaly_score=0.8,
            risk_probability=0.9,
            severity="warning",
            status="open",
            summary="daily-ratio-reportable",
            model_state="provisional",
            approval_state="draft",
            opened_at=start,
            last_seen_at=start,
            detected_at=start,
        )
        dismissed = RiskEvent(
            company_id=self.company_id,
            anomaly_score=0.8,
            risk_probability=0.9,
            severity="warning",
            status="dismissed",
            summary="daily-ratio-dismissed",
            model_state="provisional",
            approval_state="draft",
            opened_at=start,
            last_seen_at=start,
            detected_at=start,
        )
        self.db.add_all([reportable, dismissed])
        self.db.flush()
        self.db.add_all(
            [
                RiskEventArticle(risk_event_id=reportable.id, article_id=articles[0].id),
                RiskEventArticle(risk_event_id=reportable.id, article_id=articles[1].id),
                RiskEventArticle(risk_event_id=reportable.id, article_id=articles[5].id),
                RiskEventArticle(risk_event_id=dismissed.id, article_id=articles[3].id),
            ]
        )
        self.db.flush()

        update_daily_summary(self.db, self.company_id, start)
        self.db.flush()
        summary = self.db.scalar(
            select(CompanyDailySummary).where(
                CompanyDailySummary.company_id == self.company_id,
                CompanyDailySummary.summary_date
                == start.astimezone(ZoneInfo("Asia/Seoul")).date(),
            )
        )

        self.assertIsNotNone(summary)
        self.assertEqual(summary.article_count, 5)
        self.assertEqual(summary.risk_article_count, 2)
        self.assertEqual(summary.positive_article_count, 1)
        self.assertEqual(summary.neutral_article_count, 1)
        self.assertEqual(summary.negative_article_count, 2)


if __name__ == "__main__":
    unittest.main()
