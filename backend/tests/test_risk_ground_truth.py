"""Human risk review validation and downstream truth synchronization tests."""

from datetime import datetime, timedelta, timezone
import unittest
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Company,
    CompanyFeatureWindow,
    NewsArticle,
    ResponseDraft,
    RiskEvent,
    RiskEventArticle,
    RiskEventLabel,
    RiskEventType,
)
from app.training.risk_models import _if_training_windows
from app.services.risk_ground_truth import (
    apply_authoritative_risk_label,
    validate_risk_label_evidence,
)


class RiskGroundTruthDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            company_id = self.db.scalar(select(Company.id).order_by(Company.id).limit(1))
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if company_id is None:
            self.skipTest("위험 검수 테스트에 기업 한 곳이 필요합니다.")
        marker = uuid.uuid4().hex
        self.article = NewsArticle(
            source="test",
            title="검수 근거 기사",
            summary="개인정보 유출 사건",
            url=f"https://review.example/{marker}/evidence",
            original_url=f"https://review.example/{marker}/evidence",
            published_at=datetime(2096, 1, 1, tzinfo=timezone.utc),
        )
        self.unowned_article = NewsArticle(
            source="test",
            title="다른 사건 기사",
            summary="이 사건에는 연결되지 않음",
            url=f"https://review.example/{marker}/unowned",
            original_url=f"https://review.example/{marker}/unowned",
            published_at=datetime(2096, 1, 1, tzinfo=timezone.utc),
        )
        self.db.add_all([self.article, self.unowned_article])
        self.db.flush()
        self.start = datetime(2096, 1, 1, tzinfo=timezone.utc)
        self.event = RiskEvent(
            company_id=company_id,
            article_id=self.article.id,
            anomaly_score=0.8,
            risk_probability=0.9,
            severity="warning",
            status="open",
            primary_type="reputation_consumer",
            summary="모델 후보 사건",
            model_state="provisional",
            approval_state="draft",
            opened_at=self.start,
            last_seen_at=self.start + timedelta(minutes=15),
        )
        self.db.add(self.event)
        self.db.flush()
        self.db.add_all(
            [
                RiskEventArticle(
                    risk_event_id=self.event.id,
                    article_id=self.article.id,
                    evidence_score=0.2,
                ),
                RiskEventType(
                    risk_event_id=self.event.id,
                    risk_type="reputation_consumer",
                    probability=0.7,
                    is_primary=True,
                    evidence={"source": "keyword_bootstrap"},
                ),
            ]
        )
        self.db.flush()

    def tearDown(self):
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        self.db.close()

    def test_dates_and_evidence_ownership_are_validated(self):
        with self.assertRaisesRegex(ValueError, "종료 시각"):
            validate_risk_label_evidence(
                self.db,
                self.event,
                is_risk=True,
                event_start=self.start,
                event_end=self.start - timedelta(minutes=1),
                risk_types=["security_privacy"],
                evidence_article_ids=[self.article.id],
                status="confirmed",
            )
        with self.assertRaisesRegex(ValueError, "연결되지 않은"):
            validate_risk_label_evidence(
                self.db,
                self.event,
                is_risk=True,
                event_start=self.start,
                event_end=None,
                risk_types=["security_privacy"],
                evidence_article_ids=[self.unowned_article.id],
                status="confirmed",
            )
        with self.assertRaisesRegex(ValueError, "근거 기사"):
            validate_risk_label_evidence(
                self.db,
                self.event,
                is_risk=True,
                event_start=self.start,
                event_end=None,
                risk_types=["security_privacy"],
                evidence_article_ids=[],
                status="confirmed",
            )

    def test_confirmed_types_and_evidence_replace_bootstrap_truth(self):
        label = RiskEventLabel(
            risk_event_id=self.event.id,
            annotator="reviewer",
            is_risk=True,
            event_start=self.start,
            event_end=self.start + timedelta(hours=1),
            risk_types=["security_privacy", "legal_regulatory"],
            evidence_article_ids=[self.article.id],
            status="confirmed",
        )
        self.db.add(label)
        self.db.flush()

        applied = apply_authoritative_risk_label(self.db, self.event)
        self.db.flush()

        self.assertEqual(applied.id, label.id)
        self.assertEqual(self.event.primary_type, "security_privacy")
        self.assertEqual(self.event.article_id, self.article.id)
        self.assertEqual(self.event.status, "closed")
        types = list(
            self.db.scalars(
                select(RiskEventType)
                .where(RiskEventType.risk_event_id == self.event.id)
                .order_by(RiskEventType.is_primary.desc(), RiskEventType.risk_type)
            )
        )
        self.assertEqual(
            {item.risk_type for item in types},
            {"security_privacy", "legal_regulatory"},
        )
        self.assertEqual(sum(item.is_primary for item in types), 1)
        self.assertTrue(all(item.evidence["source"] == "human_review" for item in types))
        link = self.db.get(RiskEventArticle, (self.event.id, self.article.id))
        self.assertEqual(link.evidence_score, 1.0)

    def test_adjudicated_normal_label_dismisses_event_and_draft(self):
        draft = ResponseDraft(
            risk_event_id=self.event.id,
            model_name="test",
            content={},
            evidence_urls=[self.article.url],
            approval_state="draft",
        )
        label = RiskEventLabel(
            risk_event_id=self.event.id,
            annotator="adjudicator",
            is_risk=False,
            event_start=self.start,
            event_end=self.start + timedelta(minutes=15),
            risk_types=[],
            evidence_article_ids=[self.article.id],
            status="adjudicated",
        )
        self.db.add_all([draft, label])
        self.db.flush()

        apply_authoritative_risk_label(self.db, self.event)
        self.db.flush()

        self.assertEqual(self.event.status, "dismissed")
        self.assertIsNone(self.event.primary_type)
        self.assertEqual(draft.approval_state, "rejected")
        self.assertEqual(
            list(
                self.db.scalars(
                    select(RiskEventType).where(
                        RiskEventType.risk_event_id == self.event.id
                    )
                )
            ),
            [],
        )

    def test_if_training_excludes_human_confirmed_risk_period(self):
        inside = CompanyFeatureWindow(
            company_id=self.event.company_id,
            window_start=self.start,
            window_end=self.start + timedelta(minutes=15),
            data_quality="complete",
            feature_values={},
            model_state="unavailable",
        )
        outside = CompanyFeatureWindow(
            company_id=self.event.company_id,
            window_start=self.start + timedelta(hours=2),
            window_end=self.start + timedelta(hours=2, minutes=15),
            data_quality="complete",
            feature_values={},
            model_state="unavailable",
        )
        label = RiskEventLabel(
            risk_event_id=self.event.id,
            annotator="if-reviewer",
            is_risk=True,
            event_start=self.start,
            event_end=self.start + timedelta(hours=1),
            risk_types=["security_privacy"],
            evidence_article_ids=[self.article.id],
            status="confirmed",
        )
        self.db.add_all([inside, outside, label])
        self.db.flush()

        windows, excluded = _if_training_windows(self.db)

        ids = {window.id for window in windows}
        self.assertNotIn(inside.id, ids)
        self.assertIn(outside.id, ids)
        self.assertGreaterEqual(excluded, 1)

if __name__ == "__main__":
    unittest.main()
