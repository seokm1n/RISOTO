"""Unified risk/model notifications and shared promotion-gate tests."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    Company,
    CompanyFeatureWindow,
    ModelVersion,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    StoryCluster,
)
from app.routers.notifications import list_notifications
from app.services.model_governance import evaluate_model_promotion
from tests.auth_helpers import auth_for_company


class PromotionEligibilityUnitTests(unittest.TestCase):
    def test_risk_gate_is_pure_and_returns_the_state_to_apply_on_promotion(self):
        with tempfile.NamedTemporaryFile() as artifact:
            model = SimpleNamespace(
                task="risk_detector",
                artifact_path=artifact.name,
                thresholds={"model_state": "unchanged"},
            )
            readiness = {
                "tasks": [
                    {
                        "task": "risk_detector",
                        "blockers": ["최근 후보 이후 새 확정 라벨 0/20건"],
                        "class_counts": {"risk": 20, "normal": 60},
                    }
                ]
            }

            result = evaluate_model_promotion(
                SimpleNamespace(), model, readiness=readiness
            )

        self.assertTrue(result.allowed)
        self.assertEqual(result.target_model_state, "provisional")
        self.assertEqual(model.thresholds, {"model_state": "unchanged"})

    def test_reranker_cannot_be_promoted_with_weak_human_metrics(self):
        with tempfile.TemporaryDirectory() as artifact:
            model = SimpleNamespace(
                task="company_relevance_reranker",
                artifact_path=artifact,
                metrics={
                    "human_test": {
                        "precision_relevant": 0.59,
                        "recall_relevant": 0.40,
                        "roc_auc": 0.72,
                    },
                    "unseen_company_test": {
                        "precision_relevant": 0.93,
                        "recall_relevant": 0.41,
                    },
                },
            )
            readiness = {
                "tasks": [
                    {
                        "task": "company_relevance_reranker",
                        "blockers": [],
                        "class_counts": {
                            "relevant": 1278,
                            "irrelevant_or_incidental": 2658,
                        },
                    }
                ]
            }
            result = evaluate_model_promotion(
                SimpleNamespace(), model, readiness=readiness
            )

        self.assertFalse(result.allowed)
        self.assertIn("human precision", result.blocker)
        self.assertIn("unseen-company recall", result.blocker)


class NotificationDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.artifact_path: Path | None = None
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            self.company_id = self.db.scalar(
                select(Company.id).order_by(Company.id).limit(1)
            )
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.company_id is None:
            self.skipTest("알림 테스트에 기업이 필요합니다.")
        self.auth = auth_for_company(self.db, self.company_id)

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()
        if self.artifact_path is not None:
            self.artifact_path.unlink(missing_ok=True)

    def test_only_current_user_open_risks_are_returned_read_only(self):
        start = datetime(2093, 1, 1, tzinfo=timezone.utc)
        cluster = StoryCluster(
            fingerprint=uuid4().hex,
            representative_title="통합 알림 테스트 위험 스토리",
            first_published_at=start,
            last_published_at=start + timedelta(minutes=1),
        )
        self.db.add(cluster)
        self.db.flush()
        open_event = RiskEvent(
            company_id=self.company_id,
            story_cluster_id=cluster.id,
            event_source="story_v2",
            anomaly_score=0.8,
            risk_probability=0.9,
            severity="critical",
            status="open",
            summary="통합 알림 테스트 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=start,
            last_seen_at=start,
        )
        closed_event = RiskEvent(
            company_id=self.company_id,
            anomaly_score=0.7,
            risk_probability=0.8,
            severity="warning",
            status="closed",
            summary="표시되면 안 되는 종료 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=start + timedelta(minutes=1),
            last_seen_at=start + timedelta(minutes=1),
            closed_at=start + timedelta(minutes=2),
        )
        acknowledged_event = RiskEvent(
            company_id=self.company_id,
            anomaly_score=0.6,
            risk_probability=0.7,
            severity="warning",
            status="acknowledged",
            summary="확인되어 표시되면 안 되는 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=start + timedelta(minutes=2),
            last_seen_at=start + timedelta(minutes=2),
        )
        monitoring_event = RiskEvent(
            company_id=self.company_id,
            story_cluster_id=cluster.id,
            event_source="story_v2",
            anomaly_score=0.65,
            risk_probability=0.75,
            severity="warning",
            status="monitoring",
            summary="계속 감시 중인 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=start + timedelta(minutes=3),
            last_seen_at=start + timedelta(minutes=3),
        )
        self.db.add_all(
            [open_event, closed_event, acknowledged_event, monitoring_event]
        )
        singleton_event = RiskEvent(
            company_id=self.company_id,
            story_cluster_id=cluster.id,
            event_source="story_v2",
            anomaly_score=0.9,
            risk_probability=0.92,
            severity="critical",
            status="open",
            summary="기사 한 건뿐인 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=start + timedelta(minutes=4),
            last_seen_at=start + timedelta(minutes=4),
        )
        legacy_event = RiskEvent(
            company_id=self.company_id,
            anomaly_score=0.95,
            risk_probability=0.95,
            severity="critical",
            status="open",
            summary="스토리가 아닌 과거 위험",
            model_state="provisional",
            approval_state="draft",
            opened_at=start + timedelta(minutes=5),
            last_seen_at=start + timedelta(minutes=5),
        )
        self.db.add_all([singleton_event, legacy_event])
        self.db.flush()
        evidence_articles = []
        for index in range(2):
            article = NewsArticle(
                source="notification-test",
                title=f"알림 근거 기사 {index}",
                url=f"https://notification.test/{uuid4().hex}",
                published_at=start + timedelta(seconds=index),
            )
            self.db.add(article)
            self.db.flush()
            evidence_articles.append(article)
        for event in (open_event, monitoring_event):
            for article in evidence_articles:
                self.db.add(RiskEventArticle(
                    risk_event_id=event.id,
                    article_id=article.id,
                    evidence_score=0.8,
                ))
        self.db.add(RiskEventArticle(
            risk_event_id=singleton_event.id,
            article_id=evidence_articles[0].id,
            evidence_score=0.9,
        ))

        valid_count = int(
            self.db.scalar(
                select(func.count(CompanyFeatureWindow.id)).where(
                    CompanyFeatureWindow.data_quality != "unavailable"
                )
            )
            or 0
        )
        for index in range(max(0, 200 - valid_count)):
            window_start = start - timedelta(days=20, minutes=15 * index)
            self.db.add(
                CompanyFeatureWindow(
                    company_id=self.company_id,
                    window_start=window_start,
                    window_end=window_start + timedelta(minutes=15),
                    data_quality="complete",
                    feature_values={},
                    model_state="unavailable",
                )
            )

        handle, artifact_name = tempfile.mkstemp(prefix="risoto-notification-")
        Path(artifact_name).write_bytes(b"model")
        # Close the low-level descriptor created by mkstemp without deleting it.
        import os

        os.close(handle)
        self.artifact_path = Path(artifact_name)
        eligible = ModelVersion(
            task="isolation_forest",
            version=f"notification-ready-{uuid4().hex}",
            status="candidate",
            artifact_path=artifact_name,
            training_data_hash="1" * 64,
            label_schema={},
            metrics={},
            thresholds={"unchanged": True},
            training_counts={},
            created_at=start + timedelta(minutes=3),
        )
        blocked = ModelVersion(
            task="isolation_forest",
            version=f"notification-blocked-{uuid4().hex}",
            status="candidate",
            artifact_path=f"/missing/{uuid4().hex}",
            training_data_hash="2" * 64,
            label_schema={},
            metrics={},
            thresholds={},
            training_counts={},
            created_at=start + timedelta(minutes=4),
        )
        self.db.add_all([eligible, blocked])
        self.db.flush()

        response = list_notifications(self.db, self.auth)
        ids = {item.id for item in response.items}

        self.assertIn(f"risk:{open_event.id}", ids)
        self.assertNotIn(f"risk:{closed_event.id}", ids)
        self.assertNotIn(f"risk:{acknowledged_event.id}", ids)
        self.assertIn(f"risk:{monitoring_event.id}", ids)
        self.assertNotIn(f"risk:{singleton_event.id}", ids)
        self.assertNotIn(f"risk:{legacy_event.id}", ids)
        self.assertNotIn(f"model:{eligible.id}", ids)
        self.assertNotIn(f"model:{blocked.id}", ids)
        follow_up = NewsArticle(
            source="notification-test",
            title="기존 위험 스토리에 추가된 후속 기사",
            url=f"https://notification.test/{uuid4().hex}",
            published_at=start + timedelta(hours=1),
        )
        self.db.add(follow_up)
        self.db.flush()
        self.db.add(RiskEventArticle(
            risk_event_id=open_event.id,
            article_id=follow_up.id,
            evidence_score=0.7,
        ))
        self.db.flush()
        updated = list_notifications(self.db, self.auth)
        self.assertEqual(
            {item.id for item in updated.items},
            ids,
        )
        self.assertEqual(
            next(item for item in updated.items if item.id == f"risk:{open_event.id}").created_at,
            start,
        )
        self.assertEqual(response.total, len(response.items))
        self.assertEqual(
            response.risk_count,
            sum(item.type == "risk" for item in response.items),
        )
        self.assertEqual(
            response.model_promotion_count,
            sum(item.type == "model_promotion_ready" for item in response.items),
        )
        self.assertEqual(
            [item.created_at for item in response.items],
            sorted((item.created_at for item in response.items), reverse=True),
        )
        self.assertEqual(open_event.status, "open")
        self.assertEqual(eligible.status, "candidate")
        self.assertEqual(eligible.thresholds, {"unchanged": True})


if __name__ == "__main__":
    unittest.main()
