"""Successful regular/retry attempts remove only the failures they resolve."""

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import engine
from app.models import CollectionAttempt, CollectionIncident, CollectionJob, Company
from app.services.collection_health import (
    _consecutive_failures, evaluate_attempts, recover_company_incidents,
)


class CollectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        self.db = Session(bind=self.connection, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.transaction.rollback)
        self.addCleanup(self.db.close)
        owner = self.db.scalar(select(Company.user_id).limit(1))
        if owner is None:
            self.skipTest("An existing company owner is required.")
        self.companies = []
        for _ in range(2):
            name = f"recovery-{uuid4().hex}"
            company = Company(user_id=owner, name=name, normalized_name=name,
                              company_role="competitor", annual_revenue_krw=1_000_000_000,
                              company_size_class="small_medium", monitoring_status="paused",
                              analysis_status="ready")
            self.db.add(company)
            self.companies.append(company)
        self.db.flush()
        self.settings = Settings(collection_alert_webhook_url="")
        self.slot = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.source = f"recovery-{uuid4().hex[:8]}"

    def attempt(self, company, source, minute, status, *, retry=0, slot=None):
        completed = self.slot + timedelta(minutes=minute)
        scheduled = slot or completed
        job = CollectionJob(user_id=company.user_id, company_id=company.id,
                            status="completed" if status == "succeeded" else "failed",
                            job_type="realtime", sources=[source], requested_from=scheduled,
                            requested_to=completed, started_at=completed - timedelta(seconds=1),
                            completed_at=completed)
        self.db.add(job)
        self.db.flush()
        attempt = CollectionAttempt(
            user_id=company.user_id, company_id=company.id, job_id=job.id,
            source=source, scheduled_for=scheduled, attempt_number=retry,
            status=status, query_count=1, successful_query_count=int(status == "succeeded"),
            started_at=job.started_at, completed_at=completed,
            error_code="timeout" if status == "failed" else None,
        )
        self.db.add(attempt)
        self.db.flush()
        return attempt

    def evaluate(self, attempts, *, retry=False):
        result = evaluate_attempts(self.db, attempts, attempts[0].scheduled_for,
                                   self.settings, manage_incidents=not retry)
        self.db.flush()
        return result

    def test_retry_success_recovers_all_old_incidents_and_keeps_history(self):
        company = self.companies[0]
        ids = []
        for minute in (0, 15):
            _, incident_id = self.evaluate([self.attempt(company, self.source, minute, "failed")])
            ids.append(incident_id)
        success = self.attempt(company, self.source, 17, "succeeded", retry=1,
                               slot=self.slot + timedelta(minutes=15))
        self.assertEqual(self.evaluate([success], retry=True), ("complete", None))
        for incident_id in ids:
            incident = self.db.get(CollectionIncident, incident_id)
            self.assertEqual(incident.status, "recovered")
            self.assertEqual(incident.affected_company_ids, [])
            self.assertIsNotNone(incident.recovered_at)
            self.assertIsNone(incident.next_retry_at)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(CollectionIncident)
                                       .where(CollectionIncident.id.in_(ids))), 2)
        self.assertEqual(_consecutive_failures(self.db, company.id, self.source, 2), 0)

    def test_one_company_success_does_not_clear_another_company_failure(self):
        first, second = self.companies
        _, incident_id = self.evaluate([self.attempt(first, self.source, 0, "failed")])
        _, shared_id = self.evaluate([self.attempt(second, self.source, 0, "failed")])
        self.assertEqual(incident_id, shared_id)
        self.evaluate([self.attempt(first, self.source, 1, "succeeded", retry=1, slot=self.slot)], retry=True)
        incident = self.db.get(CollectionIncident, incident_id)
        self.assertEqual(incident.affected_company_ids, [second.id])
        self.assertEqual(incident.status, "retrying")
        self.evaluate([self.attempt(second, self.source, 15, "succeeded")])
        self.assertEqual(incident.status, "recovered")

    def test_sources_can_recover_in_separate_runs_without_hiding_a_new_failure(self):
        company = self.companies[0]
        source_b = self.source + "-b"
        _, incident_id = self.evaluate([
            self.attempt(company, self.source, 0, "failed"),
            self.attempt(company, source_b, 0, "failed"),
        ])
        incident = self.db.get(CollectionIncident, incident_id)
        self.evaluate([self.attempt(company, self.source, 1, "succeeded", retry=1)], retry=True)
        self.assertEqual(incident.status, "retrying")
        self.evaluate([self.attempt(company, self.source, 2, "failed", retry=2)], retry=True)
        self.evaluate([self.attempt(company, source_b, 3, "succeeded", retry=1)], retry=True)
        self.assertEqual(incident.status, "retrying")
        self.evaluate([self.attempt(company, self.source, 4, "succeeded", retry=3)], retry=True)
        self.assertEqual(incident.status, "recovered")

    def test_retry_failures_share_a_slot_and_success_resets_the_streak(self):
        company = self.companies[0]
        _, incident_id = self.evaluate([self.attempt(company, self.source, 0, "failed")])
        self.evaluate([self.attempt(company, self.source, 1, "failed", retry=1, slot=self.slot)], retry=True)
        self.assertEqual(_consecutive_failures(self.db, company.id, self.source, 2), 1)
        self.evaluate([self.attempt(company, self.source, 2, "succeeded", retry=2, slot=self.slot)], retry=True)
        self.evaluate([self.attempt(company, self.source, 15, "failed")])
        self.assertEqual(_consecutive_failures(self.db, company.id, self.source, 2), 1)
        self.assertEqual(self.db.get(CollectionIncident, incident_id).status, "recovered")

    def test_pipeline_failure_requires_success_of_the_complete_pipeline(self):
        company = self.companies[0]
        _, incident_id = self.evaluate([self.attempt(company, "pipeline", 0, "failed")])
        self.evaluate([self.attempt(company, self.source, 15, "succeeded")])
        incident = self.db.get(CollectionIncident, incident_id)
        self.assertEqual(incident.status, "retrying")
        self.assertEqual(recover_company_incidents(self.db, company.id, [], self.settings,
                                                   pipeline_succeeded=True), [incident_id])
        self.assertEqual(incident.status, "recovered")


if __name__ == "__main__":
    unittest.main()
