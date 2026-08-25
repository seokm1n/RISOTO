"""Operational risk views must hide human-dismissed false positives."""

from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Company, CompanyDailySummary, RiskEvent
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
            db=self.db,
            auth=self.auth,
        )
        legacy_list = list_risk_events(
            self.company_id,
            limit=200,
            include_legacy=True,
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


if __name__ == "__main__":
    unittest.main()
