"""모델 재학습 준비도와 일일 운영 점검의 핵심 계약 테스트."""

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import engine
from app.models import (
    ArticleLabel,
    Company,
    CompanyFeatureWindow,
    ModelVersion,
    RawNewsArticle,
)
from app.services.model_operations import (
    build_daily_model_report,
    build_training_readiness,
    ensure_daily_model_check,
    robust_distribution_shift,
)
from app.training.text_models import TextRow, _filter_eligible_rows


class ModelOperationUnitTests(unittest.TestCase):
    def test_robust_shift_requires_enough_recent_and_baseline_values(self):
        self.assertIsNone(robust_distribution_shift([1.0] * 7, [1.0] * 40))
        self.assertIsNone(robust_distribution_shift([1.0] * 8, [1.0] * 39))
        result = robust_distribution_shift([10.0] * 8, [0.0] * 40)
        self.assertIsNotNone(result)
        self.assertEqual(result["robust_z"], 10.0)

    def test_filter_training_drops_rows_masked_for_both_heads(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        masked = TextRow(1, "masked", now, "one")
        usable = TextRow(2, "usable", now, "two", relevance=0)
        self.assertEqual(_filter_eligible_rows([masked, usable]), [usable])


class ModelOperationDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            self.companies = list(self.db.scalars(select(Company).order_by(Company.id)))
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if not self.companies:
            self.skipTest("모델 운영 점검 테스트에 기업이 필요합니다.")
        for company in self.companies:
            company.monitoring_status = "paused"
        self.db.flush()
        self.settings = Settings(
            model_drift_recent_hours=2,
            model_drift_baseline_days=1,
            model_drift_robust_z_threshold=3.5,
            collection_window_minutes=15,
        )

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def _window(self, company_id: int, start: datetime, quality: str, value: float):
        self.db.add(
            CompanyFeatureWindow(
                company_id=company_id,
                window_start=start,
                window_end=start + timedelta(minutes=15),
                data_quality=quality,
                successful_sources=["test"] if quality != "unavailable" else [],
                failed_sources=[] if quality == "complete" else ["test-failure"],
                feature_values={"article_count_robust_z": value},
                model_state="unavailable",
            )
        )

    def test_paused_company_windows_do_not_create_a_false_stable_report(self):
        now = datetime(2097, 1, 2, 12, 0, tzinfo=timezone.utc)
        self._window(self.companies[0].id, now - timedelta(minutes=15), "complete", 9.0)
        self.db.flush()

        report = build_daily_model_report(self.db, now=now, settings=self.settings)

        self.assertEqual(report["monitored_company_count"], 0)
        self.assertEqual(report["recent_window_count"], 0)
        self.assertIsNone(report["collection_coverage"])
        self.assertEqual(report["status"], "insufficient_data")

    def test_drift_is_checked_per_company_and_partial_quality_warns(self):
        company = self.companies[0]
        company.monitoring_status = "active"
        now = datetime(2097, 2, 2, 12, 0, tzinfo=timezone.utc)
        recent_start = now - timedelta(hours=2)
        for index in range(40):
            self._window(
                company.id,
                recent_start - timedelta(hours=10) + timedelta(minutes=15 * index),
                "complete",
                0.0,
            )
        for index in range(8):
            self._window(
                company.id,
                recent_start + timedelta(minutes=15 * index),
                "partial" if index == 0 else "complete",
                10.0,
            )
        self.db.flush()

        report = build_daily_model_report(self.db, now=now, settings=self.settings)

        self.assertEqual(report["expected_window_count"], 8)
        self.assertEqual(report["collection_coverage"], 1.0)
        self.assertEqual(report["data_quality_counts"]["partial"], 1)
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["drift_status"], "warning")
        self.assertEqual(report["drift_flags"][0]["company_id"], company.id)
        self.assertEqual(report["drift_flags"][0]["feature"], "article_count_robust_z")

    def test_daily_check_is_idempotent_for_a_seoul_date(self):
        now = datetime(2097, 3, 2, 15, 1, tzinfo=timezone.utc)
        first = ensure_daily_model_check(self.db, now=now, settings=self.settings)
        second = ensure_daily_model_check(
            self.db, now=now + timedelta(hours=2), settings=self.settings
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.check_date, second.check_date)

    def test_sentiment_increment_excludes_non_trainable_labels(self):
        raw_ids = list(
            self.db.scalars(select(RawNewsArticle.id).order_by(RawNewsArticle.id).limit(2))
        )
        if len(raw_ids) < 2:
            self.skipTest("감성 준비도 테스트에 원문 기사 두 건이 필요합니다.")
        cutoff = datetime(2097, 4, 1, tzinfo=timezone.utc)
        self.db.add(
            ModelVersion(
                task="sentiment",
                version=f"sentiment-test-{uuid4().hex}",
                status="candidate",
                base_model="test",
                artifact_path="/tmp/not-used",
                training_data_hash="0" * 64,
                label_schema={},
                metrics={},
                thresholds={},
                training_counts={},
                created_at=cutoff,
            )
        )
        for raw_id, sentiment in zip(raw_ids, ("mixed", "positive")):
            self.db.add(
                ArticleLabel(
                    company_id=self.companies[0].id,
                    raw_article_id=raw_id,
                    annotator=f"model-ops-{uuid4().hex}",
                    relevance_label="uncertain",
                    advertisement_label="uncertain",
                    sentiment_label=sentiment,
                    status="confirmed",
                    reviewed_at=cutoff + timedelta(days=1),
                )
            )
        self.db.flush()

        readiness = build_training_readiness(self.db, settings=self.settings)
        sentiment = next(item for item in readiness["tasks"] if item["task"] == "sentiment")

        self.assertEqual(sentiment["new_since_latest"], 1)

    def test_risk_type_training_has_an_explicit_review_gate(self):
        readiness = build_training_readiness(self.db, settings=self.settings)
        task = next(
            item for item in readiness["tasks"]
            if item["task"] == "risk_type_classifier"
        )
        self.assertFalse(task["candidate_training_ready"])
        self.assertEqual(len(task["class_counts"]), 8)
        self.assertTrue(all(count >= 0 for count in task["class_counts"].values()))
        self.assertTrue(any("200건" in blocker for blocker in task["blockers"]))


if __name__ == "__main__":
    unittest.main()
