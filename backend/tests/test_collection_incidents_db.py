"""실제 PostgreSQL 트랜잭션에서 장애 병합과 부분 장애 임계값을 검증한다."""

from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base, SessionLocal
from app.models import (
    CollectionAttempt,
    CollectionIncident,
    CollectionJob,
    Company,
    NotificationDelivery,
    User,
)
from app.routers.operations import collection_health
from app.services.collection_health import evaluate_attempts
from tests.auth_helpers import auth_for_user


class CollectionIncidentDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            self.user_id = self.db.scalar(
                select(Company.user_id).order_by(Company.id).limit(1)
            )
            self.company_ids = list(
                self.db.scalars(
                    select(Company.id)
                    .where(Company.user_id == self.user_id)
                    .order_by(Company.id)
                    .limit(2)
                )
            )
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if len(self.company_ids) < 2:
            self.skipTest("장애 병합 테스트에 기업 두 곳이 필요합니다.")
        self.auth = auth_for_user(self.db, self.user_id)
        self.settings = Settings(
            collection_alert_webhook_url="https://webhook.invalid/collection",
            collection_retry_delays_seconds="60,300,900",
            partial_failure_consecutive_threshold=2,
        )

    def tearDown(self):
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        self.db.close()

    def attempt(
        self,
        company_id: int,
        source: str,
        scheduled_for: datetime,
        status: str,
    ) -> CollectionAttempt:
        job = CollectionJob(
            user_id=self.user_id,
            company_id=company_id,
            status="failed" if status == "failed" else "completed",
            job_type="realtime",
            sources=[source],
            requested_from=scheduled_for - timedelta(hours=1),
            requested_to=scheduled_for,
            started_at=scheduled_for,
            completed_at=scheduled_for + timedelta(seconds=1),
        )
        self.db.add(job)
        self.db.flush()
        attempt = CollectionAttempt(
            user_id=self.user_id,
            job_id=job.id,
            company_id=company_id,
            source=source,
            scheduled_for=scheduled_for,
            attempt_number=0,
            status=status,
            query_count=1,
            successful_query_count=1 if status == "succeeded" else 0,
            fetched_count=0,
            error_code="timeout" if status == "failed" else None,
            error_message="timeout api_key=must-not-leak" if status == "failed" else None,
            started_at=scheduled_for,
            completed_at=scheduled_for + timedelta(seconds=1),
        )
        self.db.add(attempt)
        self.db.flush()
        return attempt

    def test_same_slot_and_cause_merge_companies_before_delivery(self):
        # 15:00 UTC is midnight in Seoul on the following day.
        slot = datetime(2099, 1, 1, 15, 0, tzinfo=timezone.utc)
        first = self.attempt(self.company_ids[0], "naver_api_hub", slot, "failed")
        second = self.attempt(self.company_ids[1], "naver_api_hub", slot, "failed")
        _, first_incident_id = evaluate_attempts(self.db, [first], slot, self.settings)
        _, second_incident_id = evaluate_attempts(self.db, [second], slot, self.settings)
        self.assertEqual(first_incident_id, second_incident_id)
        incident = self.db.get(CollectionIncident, first_incident_id)
        self.assertEqual(incident.affected_company_ids, sorted(self.company_ids))
        self.assertEqual(incident.data_quality, "unavailable")
        self.assertEqual(incident.retry_count, 0)
        self.assertIsNotNone(incident.next_retry_at)
        self.db.flush()
        delivery = self.db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.incident_id == incident.id)
        )
        self.assertEqual(delivery.payload["affected_company_ids"], sorted(self.company_ids))
        self.assertEqual(delivery.payload["message"], "00:00 수집 구간 실패")
        self.assertNotIn("must-not-leak", delivery.payload["error_summary"])

    def test_partial_failure_alerts_on_second_distinct_window(self):
        company_id = self.company_ids[0]
        first_slot = datetime(2099, 2, 1, 0, 0, tzinfo=timezone.utc)
        first_failed = self.attempt(company_id, "kakao_daum", first_slot, "failed")
        first_success = self.attempt(company_id, "naver_api_hub", first_slot, "succeeded")
        quality, incident_id = evaluate_attempts(
            self.db, [first_failed, first_success], first_slot, self.settings
        )
        self.assertEqual(quality, "partial")
        self.assertIsNotNone(incident_id)
        first_incident_id = incident_id
        self.assertIsNone(
            self.db.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.incident_id == first_incident_id
                )
            )
        )

        second_slot = first_slot + timedelta(minutes=15)
        second_failed = self.attempt(company_id, "kakao_daum", second_slot, "failed")
        second_success = self.attempt(company_id, "naver_api_hub", second_slot, "succeeded")
        quality, incident_id = evaluate_attempts(
            self.db, [second_failed, second_success], second_slot, self.settings
        )
        self.assertEqual(quality, "partial")
        self.assertIsNotNone(incident_id)
        self.assertNotEqual(incident_id, first_incident_id)
        incident = self.db.get(CollectionIncident, incident_id)
        self.assertEqual(incident.sources, ["kakao_daum"])
        self.assertIsNone(incident.next_retry_at)

    def test_recovered_synthetic_pipeline_attempt_does_not_keep_health_down(self):
        # Isolate the health calculation from committed incidents while this
        # transaction is active; rollback restores every original row.
        existing_open = list(
            self.db.scalars(
                select(CollectionIncident).where(
                    CollectionIncident.user_id == self.user_id,
                    CollectionIncident.status.in_(["open", "retrying"])
                )
            )
        )
        for incident in existing_open:
            incident.status = "recovered"
            incident.recovered_at = datetime.now(timezone.utc)
            incident.next_retry_at = None

        future_slot = datetime(2100, 1, 1, tzinfo=timezone.utc)
        current_pairs = set(self.db.execute(
            select(CollectionAttempt.company_id, CollectionAttempt.source).where(
                CollectionAttempt.user_id == self.user_id,
                CollectionAttempt.source != "pipeline",
            ).distinct()
        ).all())
        current_pairs.add((self.company_ids[0], "naver_api_hub"))
        for company_id, source in sorted(current_pairs):
            self.attempt(company_id, source, future_slot, "succeeded")
        self.attempt(
            self.company_ids[0],
            "pipeline",
            future_slot + timedelta(minutes=15),
            "failed",
        )
        pipeline_incident = CollectionIncident(
            user_id=self.user_id,
            fingerprint="f" * 64,
            status="recovered",
            data_quality="unavailable",
            severity="critical",
            scheduled_for=future_slot + timedelta(minutes=15),
            detected_at=future_slot + timedelta(minutes=16),
            last_seen_at=future_slot + timedelta(minutes=16),
            affected_company_ids=[],
            sources=["pipeline"],
            error_summary="pipeline: database integrity conflict",
            retry_count=3,
            next_retry_at=None,
            recovered_at=future_slot + timedelta(minutes=17),
        )
        self.db.add(pipeline_incident)
        self.db.flush()

        recovered_health = collection_health(self.db, self.auth)

        self.assertEqual(recovered_health.open_incident_count, 0)
        self.assertEqual(recovered_health.status, "healthy")
        self.assertNotIn("pipeline", {item.source for item in recovered_health.sources})

        # The same synthetic failure must still affect overall health while its
        # incident is actionable, even though it is not a collector row.
        pipeline_incident.status = "retrying"
        pipeline_incident.recovered_at = None
        pipeline_incident.next_retry_at = future_slot + timedelta(minutes=20)
        self.db.flush()

        active_health = collection_health(self.db, self.auth)

        self.assertEqual(active_health.open_incident_count, 1)
        self.assertEqual(active_health.status, "unavailable")
        self.assertNotIn("pipeline", {item.source for item in active_health.sources})

    def test_health_reports_current_failure_and_ignores_deleted_company_incident(self):
        existing_open = list(
            self.db.scalars(
                select(CollectionIncident).where(
                    CollectionIncident.user_id == self.user_id,
                    CollectionIncident.status.in_(["open", "retrying"])
                )
            )
        )
        for incident in existing_open:
            incident.status = "recovered"
            incident.recovered_at = datetime.now(timezone.utc)
            incident.next_retry_at = None

        future_slot = datetime(2101, 1, 1, tzinfo=timezone.utc)
        current_pairs = set(self.db.execute(
            select(CollectionAttempt.company_id, CollectionAttempt.source).where(
                CollectionAttempt.user_id == self.user_id,
                CollectionAttempt.source != "pipeline",
            ).distinct()
        ).all())
        for company_id, source in sorted(current_pairs):
            self.attempt(company_id, source, future_slot, "succeeded")
        self.attempt(self.company_ids[0], "youtube_comment", future_slot, "failed")
        self.attempt(self.company_ids[1], "youtube_comment", future_slot, "succeeded")

        self.db.add(
            CollectionIncident(
                user_id=self.user_id,
                fingerprint="d" * 64,
                status="open",
                data_quality="unavailable",
                severity="critical",
                scheduled_for=future_slot,
                detected_at=future_slot + timedelta(minutes=1),
                last_seen_at=future_slot + timedelta(minutes=1),
                affected_company_ids=[999_999_999],
                sources=["naver_api_hub"],
                error_summary="401 Unauthorized",
                retry_count=0,
                next_retry_at=None,
            )
        )
        self.db.flush()

        health = collection_health(self.db, self.auth)
        youtube = next(item for item in health.sources if item.source == "youtube_comment")

        self.assertEqual(health.open_incident_count, 0)
        self.assertEqual(health.status, "degraded")
        self.assertEqual(youtube.status, "partial")
        self.assertEqual(youtube.consecutive_failures, 1)
        self.assertEqual(youtube.last_error_code, "timeout")
        self.assertIn("[REDACTED]", youtube.last_error_message)
        self.assertNotIn("must-not-leak", youtube.last_error_message)


class CollectionSourceHealthTests(unittest.TestCase):
    """Exercise source-state recovery in an isolated in-memory database."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        # SQLite ignores this PostgreSQL-only partial-index predicate, making
        # it incorrectly reject two competitor companies for the same user.
        next(index for index in Company.__table__.indexes
             if index.name == "uq_companies_one_main_per_user").drop(self.engine)
        self.db = Session(self.engine)
        self.db.add(User(id=1, email="source-health@example.test", password_hash="test-only"))
        self.db.add_all([
            Company(id=company_id, user_id=1, name=f"Health {company_id}",
                    normalized_name=f"health{company_id}", company_role="competitor",
                    annual_revenue_krw=1_000_000_000, company_size_class="small_medium",
                    monitoring_status="active", analysis_status="ready")
            for company_id in (1, 2)
        ])
        self.db.flush()
        self.auth = auth_for_user(self.db, 1)
        self.slot = datetime(2099, 1, 1, tzinfo=timezone.utc)
        self.next_id = 1

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def attempt(self, company_id, status, *, slot=None, delay=0, attempt_number=0):
        slot = slot or self.slot
        started_at = slot + timedelta(seconds=delay)
        job_id = self.next_id
        self.next_id += 1
        self.db.add(CollectionJob(
            id=job_id, user_id=1, company_id=company_id,
            status="failed" if status == "failed" else "completed",
            job_type="realtime", sources=["naver_api_hub"],
            requested_from=slot - timedelta(minutes=15), requested_to=slot,
            started_at=started_at, completed_at=started_at + timedelta(seconds=1),
        ))
        self.db.add(CollectionAttempt(
            id=job_id, user_id=1, job_id=job_id, company_id=company_id,
            source="naver_api_hub", scheduled_for=slot, attempt_number=attempt_number,
            status=status, query_count=1, successful_query_count=int(status == "succeeded"),
            fetched_count=0, error_code="timeout" if status == "failed" else None,
            error_message="timeout api_key=must-not-leak" if status == "failed" else None,
            started_at=started_at, completed_at=started_at + timedelta(seconds=1),
        ))
        self.db.flush()

    def source_health(self):
        return collection_health(self.db, self.auth).sources[0]

    def test_same_window_retry_success_clears_source_failure(self):
        self.attempt(1, "failed")
        self.attempt(1, "succeeded", delay=60, attempt_number=1)

        health = collection_health(self.db, self.auth)
        source = health.sources[0]

        self.assertEqual(health.status, "healthy")
        self.assertEqual(source.status, "healthy")
        self.assertEqual(source.consecutive_failures, 0)
        self.assertIsNone(source.last_error_code)
        self.assertIsNone(source.last_error_message)
        self.assertEqual(source.last_success_at, source.last_attempt_at)

    def test_retry_recovery_does_not_hide_other_company_failure(self):
        self.attempt(1, "failed")
        self.attempt(2, "failed")
        self.attempt(1, "succeeded", delay=60, attempt_number=1)

        health = collection_health(self.db, self.auth)
        source = health.sources[0]

        self.assertEqual(health.status, "degraded")
        self.assertEqual(source.status, "partial")
        self.assertEqual(source.consecutive_failures, 1)
        self.assertEqual(source.last_error_code, "timeout")
        self.assertIn("[REDACTED]", source.last_error_message)
        self.assertNotIn("must-not-leak", source.last_error_message)

    def test_newer_failure_after_recovery_counts_distinct_windows(self):
        self.attempt(1, "succeeded")
        failed_slot = self.slot + timedelta(minutes=15)
        self.attempt(1, "failed", slot=failed_slot)
        self.attempt(1, "failed", slot=failed_slot, delay=60, attempt_number=1)

        source = self.source_health()
        self.assertEqual(source.status, "partial")
        self.assertEqual(source.consecutive_failures, 1)
        self.assertEqual(source.last_error_code, "timeout")

        self.attempt(1, "failed", slot=failed_slot + timedelta(minutes=15))
        source = self.source_health()
        self.assertEqual(source.status, "down")
        self.assertEqual(source.consecutive_failures, 2)

    def test_late_retry_of_older_slot_uses_actual_completion_time(self):
        self.attempt(1, "failed", slot=self.slot + timedelta(minutes=15))
        self.attempt(1, "succeeded", delay=20 * 60, attempt_number=1)

        self.assertEqual(self.source_health().status, "healthy")

    def test_later_inserted_old_success_does_not_clear_newer_failure(self):
        self.attempt(1, "failed", delay=120)
        self.attempt(1, "succeeded", delay=60, attempt_number=1)

        source = self.source_health()
        self.assertEqual(source.status, "partial")
        self.assertEqual(source.consecutive_failures, 1)
        self.assertEqual(source.last_error_code, "timeout")

    def test_busy_company_does_not_hide_another_company_failure(self):
        self.attempt(2, "failed")
        for index in range(201):
            self.attempt(1, "succeeded", delay=index + 1, attempt_number=1)

        source = self.source_health()
        self.assertEqual(source.status, "partial")
        self.assertEqual(source.consecutive_failures, 1)
        self.assertEqual(source.last_error_code, "timeout")


if __name__ == "__main__":
    unittest.main()
