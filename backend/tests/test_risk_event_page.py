"""위험관리 페이지네이션과 비동기 개별 생성 API 계약."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Company, RiskEvent, RiskEventType
from app.routers.collection import list_risk_events_page
from app.routers.governance import start_response_generation
from tests.auth_helpers import auth_for_company


class RiskEventPageDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        owner_company = self.db.scalar(select(Company).order_by(Company.id).limit(1))
        if owner_company is None:
            self.db.close()
            self.skipTest("위험관리 API 테스트에 회원 한 명이 필요합니다.")
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        self.company = Company(
            user_id=owner_company.user_id,
            name=f"risk-page-test-{suffix}",
            normalized_name=f"risk-page-test-{suffix}",
            company_role="competitor",
            annual_revenue_krw=1_000_000_000,
            company_size_class="small_medium",
            monitoring_status="active",
            analysis_status="ready",
        )
        self.db.add(self.company)
        self.db.commit()
        self.auth = auth_for_company(self.db, self.company.id)

    def tearDown(self):
        if hasattr(self, "company"):
            company = self.db.get(Company, self.company.id)
            if company is not None:
                self.db.delete(company)
                self.db.commit()
        self.db.close()

    def event(self, index: int, *, status: str, severity: str, response_status: str) -> RiskEvent:
        timestamp = datetime(2099, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        event = RiskEvent(
            company_id=self.company.id,
            event_key=f"story-v3:{self.company.id}:page-test-{index}",
            event_source="story_v2",
            anomaly_score=0.5,
            risk_probability=0.7 + index / 100,
            severity=severity,
            status=status,
            primary_type="security_privacy",
            summary=f"스토리 사건 {index}",
            model_state="provisional",
            approval_state="draft",
            response_generation_status=response_status,
            opened_at=timestamp,
            last_seen_at=timestamp,
            last_evidence_at=timestamp,
            closed_at=timestamp if status == "closed" else None,
            detected_at=timestamp,
        )
        self.db.add(event)
        self.db.flush()
        self.db.add(RiskEventType(
            risk_event_id=event.id,
            risk_type="security_privacy",
            probability=0.9,
            is_primary=True,
            evidence={"source": "test"},
        ))
        return event

    def test_page_filters_summary_and_fixed_sorting(self):
        open_event = self.event(1, status="open", severity="warning", response_status="deferred")
        generating = self.event(2, status="monitoring", severity="warning", response_status="generating")
        critical = self.event(3, status="acknowledged", severity="critical", response_status="generated")
        closed = self.event(4, status="closed", severity="warning", response_status="generated")
        self.event(5, status="legacy_candidate", severity="critical", response_status="failed")
        self.event(6, status="dismissed", severity="critical", response_status="failed")
        self.db.commit()

        result = list_risk_events_page(
            self.company.id,
            view="active",
            page=1,
            page_size=2,
            days=None,
            severity=None,
            risk_type=None,
            response="all",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual(result.total, 3)
        self.assertEqual([item.id for item in result.items], [critical.id, generating.id])
        self.assertEqual(result.summary.active, 3)
        self.assertEqual(result.summary.critical, 1)
        self.assertEqual(result.summary.needs_response, 1)
        self.assertEqual(result.summary.history, 1)

        needs_response = list_risk_events_page(
            self.company.id,
            view="active",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type="security_privacy",
            response="needs_action",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual([item.id for item in needs_response.items], [open_event.id])

        history = list_risk_events_page(
            self.company.id,
            view="history",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type=None,
            response="all",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual([item.id for item in history.items], [closed.id])

    @patch("app.routers.governance.enqueue_response_draft")
    def test_response_generation_is_idempotent_while_pending(self, enqueue):
        event = self.event(1, status="open", severity="warning", response_status="deferred")
        self.db.commit()

        first = start_response_generation(event.id, force=False, db=self.db, auth=self.auth)
        second = start_response_generation(event.id, force=False, db=self.db, auth=self.auth)

        self.assertEqual(first.status, "pending")
        self.assertEqual(second.status, "pending")
        enqueue.assert_called_once_with(event.id, force=False)


if __name__ == "__main__":
    unittest.main()
