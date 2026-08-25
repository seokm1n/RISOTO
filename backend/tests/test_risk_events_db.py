"""실제 DB 트랜잭션에서 위험 사건 개방·병합·종료 히스테리시스를 검증한다."""

from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal
from app.models import Company, CompanyFeatureWindow, RiskEvent, RiskEventType
from app.services.risk_analysis import update_risk_events


class RiskEventDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            self.company_id = self.db.scalar(select(Company.id).order_by(Company.id).limit(1))
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.company_id is None:
            self.skipTest("위험 사건 테스트에 기업 한 곳이 필요합니다.")
        self.settings = Settings(risk_close_consecutive_windows=2)
        self.start = datetime(2097, 1, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        self.db.close()

    def window(self, offset: int, is_risk: bool, risk_type: str) -> CompanyFeatureWindow:
        start = self.start + timedelta(minutes=15 * offset)
        window = CompanyFeatureWindow(
            company_id=self.company_id,
            window_start=start,
            window_end=start + timedelta(minutes=15),
            data_quality="complete",
            risk_probability=0.9 if is_risk else 0.1,
            decision_threshold=0.65,
            is_risk=is_risk,
            anomaly_score=0.5,
            risk_type_scores={risk_type: 0.8},
            feature_values={},
            model_state="provisional",
            model_version="test-risk-model",
        )
        self.db.add(window)
        self.db.flush()
        return window

    def test_first_window_opens_then_merges_type_change_and_closes_after_two_lows(self):
        opened = update_risk_events(
            self.db,
            self.window(0, True, "product_quality"),
            self.settings,
        )
        self.assertIsNotNone(opened)
        event_id, should_generate = opened
        self.assertTrue(should_generate)
        event = self.db.get(RiskEvent, event_id)
        self.assertEqual(event.status, "open")

        changed = update_risk_events(
            self.db,
            self.window(1, True, "security_privacy"),
            self.settings,
        )
        self.assertEqual(changed, (event_id, True))
        self.assertEqual(event.primary_type, "security_privacy")
        primaries = list(
            self.db.scalars(
                select(RiskEventType).where(
                    RiskEventType.risk_event_id == event_id,
                    RiskEventType.is_primary.is_(True),
                )
            )
        )
        self.assertEqual([item.risk_type for item in primaries], ["security_privacy"])

        middle = self.window(2, False, "security_privacy")
        middle.risk_probability = 0.5
        update_risk_events(
            self.db,
            middle,
            self.settings,
        )
        self.assertNotEqual(event.status, "closed")
        self.assertEqual(event.consecutive_below, 0)
        update_risk_events(
            self.db,
            self.window(3, False, "security_privacy"),
            self.settings,
        )
        self.assertNotEqual(event.status, "closed")
        self.assertEqual(event.consecutive_below, 1)
        update_risk_events(
            self.db,
            self.window(4, False, "security_privacy"),
            self.settings,
        )
        self.assertEqual(event.status, "closed")
        self.assertEqual(event.closed_at, self.start + timedelta(minutes=75))

    def test_unrelated_signal_after_a_gap_opens_a_separate_event(self):
        first_id, _ = update_risk_events(
            self.db,
            self.window(0, True, "product_quality"),
            self.settings,
        )

        second_id, should_generate = update_risk_events(
            self.db,
            self.window(8, True, "security_privacy"),
            self.settings,
        )

        self.assertNotEqual(first_id, second_id)
        self.assertTrue(should_generate)


if __name__ == "__main__":
    unittest.main()
