"""Workspace-scoped APIs must never expose another tenant's records."""

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    CollectionIncident,
    CollectionJob,
    Company,
    ResponseDraft,
    RiskEvent,
    User,
    Workspace,
    WorkspaceMember,
)
from app.routers.collection import list_collection_jobs, list_risk_events
from app.routers.companies import get_company, list_companies
from app.routers.governance import list_response_drafts
from app.routers.notifications import list_notifications
from app.routers.operations import list_collection_incidents
from tests.auth_helpers import auth_for_company, auth_for_workspace


class WorkspaceIsolationDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            self.first_company = self.db.scalar(
                select(Company).order_by(Company.id).limit(1)
            )
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.first_company is None:
            self.skipTest("워크스페이스 격리 테스트에 기업이 필요합니다.")
        self.first_auth = auth_for_company(self.db, self.first_company.id)

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def test_companies_risks_jobs_incidents_notifications_and_drafts_are_isolated(self):
        suffix = uuid4().hex
        user = User(email=f"isolation-{suffix}@example.com", password_hash="unused")
        workspace = Workspace(name=f"격리 공간 {suffix}")
        self.db.add_all([user, workspace])
        self.db.flush()
        self.db.add(
            WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="member")
        )
        # The same normalized name and ticker are valid in another workspace.
        other_company = Company(
            workspace_id=workspace.id,
            name=self.first_company.name,
            normalized_name=self.first_company.normalized_name,
            ticker=self.first_company.ticker,
            company_role="main",
            annual_revenue_krw=123_456_000_000,
            company_size_class="mid_sized",
            industry_id=self.first_company.industry_id,
            monitoring_status="paused",
            analysis_status="pending",
        )
        self.db.add(other_company)
        self.db.flush()
        other_auth = auth_for_workspace(self.db, workspace.id)

        now = datetime(2097, 1, 1, tzinfo=timezone.utc)
        event = RiskEvent(
            company_id=other_company.id,
            anomaly_score=0.9,
            risk_probability=0.9,
            severity="critical",
            status="open",
            summary="다른 워크스페이스 전용 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=now,
            last_seen_at=now,
        )
        job = CollectionJob(
            workspace_id=workspace.id,
            company_id=other_company.id,
            status="completed",
            job_type="manual",
            sources=["naver_api_hub"],
            requested_from=now,
            requested_to=now,
            started_at=now,
            completed_at=now,
        )
        incident = CollectionIncident(
            workspace_id=workspace.id,
            fingerprint="e" * 64,
            status="open",
            data_quality="unavailable",
            severity="critical",
            scheduled_for=now,
            detected_at=now,
            last_seen_at=now,
            affected_company_ids=[other_company.id],
            sources=["naver_api_hub"],
            error_summary="test failure",
        )
        self.db.add_all([event, job, incident])
        self.db.flush()
        draft = ResponseDraft(
            risk_event_id=event.id,
            workspace_id=workspace.id,
            source_company_id=other_company.id,
            target_main_company_id=other_company.id,
            generation_kind="main_response",
            schema_version=2,
            model_name="test",
            content={},
            evidence_urls=[],
            approval_state="draft",
        )
        self.db.add(draft)
        self.db.flush()

        for call in (
            lambda: get_company(other_company.id, self.db, self.first_auth),
            lambda: list_collection_jobs(
                other_company.id, page=1, page_size=10, db=self.db, auth=self.first_auth
            ),
            lambda: list_risk_events(
                other_company.id,
                limit=50,
                include_legacy=False,
                db=self.db,
                auth=self.first_auth,
            ),
            lambda: list_response_drafts(event.id, self.db, self.first_auth),
        ):
            with self.assertRaises(HTTPException) as hidden:
                call()
            self.assertEqual(hidden.exception.status_code, 404)

        first_company_ids = {
            company.id for company in list_companies(self.db, self.first_auth)
        }
        self.assertNotIn(other_company.id, first_company_ids)
        notification_ids = {
            item.id for item in list_notifications(self.db, self.first_auth).items
        }
        self.assertNotIn(f"risk:{event.id}", notification_ids)
        first_incident_ids = {
            item.id
            for item in list_collection_incidents(
                status=None,
                page=1,
                page_size=100,
                db=self.db,
                auth=self.first_auth,
            ).items
        }
        self.assertNotIn(incident.id, first_incident_ids)

        self.assertEqual(get_company(other_company.id, self.db, other_auth).id, other_company.id)
        self.assertEqual(
            list_collection_jobs(
                other_company.id, page=1, page_size=10, db=self.db, auth=other_auth
            ).items[0].id,
            job.id,
        )
        self.assertEqual(
            list_response_drafts(event.id, self.db, other_auth)[0].id,
            draft.id,
        )


if __name__ == "__main__":
    unittest.main()
