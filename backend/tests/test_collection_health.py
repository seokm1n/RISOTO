"""수집 구간 상태, 오류 비밀 제거와 재시도 판정의 단위 테스트."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.collection_health import (
    data_quality_for,
    dispatch_pending_notifications,
    floor_window,
    sanitize_error,
)
from app.schemas import CollectionIncidentRead, CollectionJobRead


class CollectionHealthTests(unittest.TestCase):
    def test_valid_zero_result_is_complete(self):
        attempts = [SimpleNamespace(status="succeeded")]
        self.assertEqual(data_quality_for(attempts), "complete")

    def test_all_failed_is_unavailable_not_zero_articles(self):
        attempts = [SimpleNamespace(status="failed"), SimpleNamespace(status="failed")]
        self.assertEqual(data_quality_for(attempts), "unavailable")

    def test_mixed_sources_are_partial(self):
        attempts = [SimpleNamespace(status="succeeded"), SimpleNamespace(status="failed")]
        self.assertEqual(data_quality_for(attempts), "partial")

    def test_window_is_aligned_to_quarter_hour(self):
        value = datetime(2026, 8, 20, 0, 14, 59, tzinfo=timezone.utc)
        self.assertEqual(floor_window(value), datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc))

    def test_error_summary_redacts_credentials(self):
        cleaned = sanitize_error("timeout api_key=real-secret token:abc123 client_secret=qwerty")
        self.assertNotIn("real-secret", cleaned)
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("qwerty", cleaned)
        self.assertEqual(cleaned.count("[REDACTED]"), 3)

    def test_error_summary_collapses_database_sql_and_parameters(self):
        raw = (
            "pipeline: IntegrityError: (psycopg.errors.UniqueViolation) "
            "duplicate key value violates unique constraint "
            '"uq_news_articles_raw_article_id" DETAIL: Key (raw_article_id)=(12357) '
            "already exists. [SQL: INSERT INTO news_articles (...) VALUES (...)] "
            "[parameters: {'raw_article_id': 12357, 'api_key': 'private'}]"
        )

        cleaned = sanitize_error(raw)

        self.assertEqual(cleaned, "pipeline: 데이터 저장 충돌")
        self.assertNotIn("12357", cleaned)
        self.assertNotIn("INSERT", cleaned)
        self.assertNotIn("parameters", cleaned)

    def test_authorization_bearer_value_is_fully_redacted(self):
        cleaned = sanitize_error("request failed Authorization: Bearer abc.def.ghi")
        self.assertEqual(cleaned, "request failed Authorization: [REDACTED]")
        self.assertNotIn("abc.def.ghi", cleaned)

    def test_public_incident_and_job_schemas_sanitize_historical_rows(self):
        now = datetime.now(timezone.utc)
        unsafe = (
            "pipeline: duplicate key value violates unique constraint "
            '"uq_news_articles_raw_article_id" DETAIL: Key (raw_article_id)=(12357)'
        )
        incident = CollectionIncidentRead.model_validate(
            SimpleNamespace(
                id=27,
                status="retrying",
                data_quality="unavailable",
                severity="critical",
                scheduled_for=now,
                detected_at=now,
                last_seen_at=now,
                affected_company_ids=[14],
                sources=["pipeline"],
                error_summary=unsafe,
                retry_count=1,
                next_retry_at=now,
                notified_at=now,
                recovered_at=None,
                acknowledged_at=None,
            )
        )
        job = CollectionJobRead.model_validate(
            SimpleNamespace(
                id=99,
                company_id=14,
                status="failed",
                job_type="realtime",
                sources=["pipeline"],
                query_count=0,
                fetched_count=0,
                new_count=0,
                matched_count=0,
                errors=[{"source": "pipeline", "message": unsafe}],
                requested_from=now,
                requested_to=now,
                started_at=now,
                completed_at=now,
            )
        )

        self.assertEqual(
            incident.error_summary,
            "pipeline: 데이터 저장 충돌",
        )
        self.assertEqual(
            job.errors[0]["message"],
            "pipeline: 데이터 저장 충돌",
        )

    def test_previously_sanitized_english_database_error_is_localized(self):
        now = datetime.now(timezone.utc)
        historical_row = CollectionIncidentRead.model_validate(
            SimpleNamespace(
                id=27,
                status="recovered",
                data_quality="unavailable",
                severity="critical",
                scheduled_for=now,
                detected_at=now,
                last_seen_at=now,
                affected_company_ids=[],
                sources=["pipeline"],
                error_summary="pipeline: database integrity conflict",
                retry_count=3,
                next_retry_at=None,
                notified_at=None,
                recovered_at=now,
                acknowledged_at=None,
            )
        )
        self.assertEqual(historical_row.error_summary, "pipeline: 데이터 저장 충돌")

    def test_webhook_failure_is_persisted_without_raising(self):
        delivery = SimpleNamespace(
            endpoint="https://webhook.invalid",
            payload={"message": "test"},
            status="pending",
            attempt_count=0,
            response_code=None,
            error_message=None,
            next_retry_at=datetime.now(timezone.utc),
            delivered_at=None,
        )

        class FakeSession:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def scalars(self, _query): return [delivery]
            def commit(self): self.committed = True

        settings = SimpleNamespace(collection_alert_webhook_timeout_seconds=0.1)
        with patch("app.services.collection_health.SessionLocal", return_value=FakeSession()), patch(
            "app.services.collection_health.httpx.post",
            side_effect=RuntimeError("network token=private-value"),
        ):
            delivered = dispatch_pending_notifications(settings)
        self.assertEqual(delivered, 0)
        self.assertEqual(delivery.status, "failed")
        self.assertEqual(delivery.attempt_count, 1)
        self.assertIsNotNone(delivery.next_retry_at)
        self.assertNotIn("private-value", delivery.error_message)


if __name__ == "__main__":
    unittest.main()
