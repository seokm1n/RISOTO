"""Public review payloads work without exposing or accepting person names."""

from datetime import datetime, timezone
import unittest

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    ArticleFilterResult,
    ArticleLabel,
    Company,
    ResponseDraft,
    RiskEvent,
)
from app.routers.governance import approve_response_draft
from app.routers.reviews import save_article_label, save_risk_event_label
from app.schemas import (
    ArticleLabelCreate,
    ArticleLabelRead,
    ResponseDraftRead,
    ResponseDraftReview,
    RiskEventLabelCreate,
    RiskEventLabelRead,
)
from app.services.review_identity import INTERNAL_REVIEW_ACTOR


class AnonymousReviewSchemaTests(unittest.TestCase):
    def test_legacy_actor_fields_are_ignored_and_not_exposed(self):
        article = ArticleLabelCreate.model_validate(
            {
                "company_id": 1,
                "raw_article_id": 2,
                "annotator": "legacy-person-name",
                "relevance_label": "relevant",
                "advertisement_label": "no",
                "sentiment_label": "neutral",
            }
        )
        risk = RiskEventLabelCreate.model_validate(
            {
                "reviewer": "legacy-person-name",
                "annotator": "legacy-person-name",
                "is_risk": False,
                "event_start": datetime(2095, 1, 1, tzinfo=timezone.utc),
            }
        )
        draft = ResponseDraftReview.model_validate(
            {"reviewer": "legacy-person-name"}
        )

        self.assertNotIn("annotator", article.model_dump())
        self.assertNotIn("annotator", risk.model_dump())
        self.assertNotIn("reviewer", draft.model_dump())
        self.assertNotIn("annotator", ArticleLabelRead.model_fields)
        self.assertNotIn("annotator", RiskEventLabelRead.model_fields)
        self.assertNotIn("reviewed_by", ResponseDraftRead.model_fields)


class AnonymousReviewDatabaseTests(unittest.TestCase):
    def setUp(self):
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
            self.skipTest("검수 API 테스트에 기업이 필요합니다.")

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def test_article_risk_and_draft_reviews_use_internal_actor(self):
        candidate = self.db.execute(
            select(
                ArticleFilterResult.company_id,
                ArticleFilterResult.raw_article_id,
            )
            .where(
                ~exists(
                    select(ArticleLabel.id).where(
                        ArticleLabel.company_id == ArticleFilterResult.company_id,
                        ArticleLabel.raw_article_id == ArticleFilterResult.raw_article_id,
                        ArticleLabel.annotator == INTERNAL_REVIEW_ACTOR,
                    )
                )
            )
            .limit(1)
        ).first()
        if candidate is None:
            self.skipTest("기사 검수 후보가 없습니다.")

        article_label = save_article_label(
            ArticleLabelCreate(
                company_id=candidate.company_id,
                raw_article_id=candidate.raw_article_id,
                relevance_label="relevant",
                advertisement_label="no",
                sentiment_label="neutral",
            ),
            self.db,
        )
        self.assertEqual(article_label.annotator, INTERNAL_REVIEW_ACTOR)

        started_at = datetime(2095, 2, 1, tzinfo=timezone.utc)
        event = RiskEvent(
            company_id=self.company_id,
            anomaly_score=0.7,
            risk_probability=0.6,
            severity="warning",
            status="open",
            model_state="provisional",
            approval_state="draft",
            opened_at=started_at,
            last_seen_at=started_at,
        )
        self.db.add(event)
        self.db.flush()
        risk_label = save_risk_event_label(
            event.id,
            RiskEventLabelCreate(is_risk=False, event_start=started_at),
            self.db,
        )
        self.assertEqual(risk_label.annotator, INTERNAL_REVIEW_ACTOR)

        response_draft = ResponseDraft(
            risk_event_id=event.id,
            model_name="test",
            content={},
            evidence_urls=[],
            approval_state="draft",
        )
        self.db.add(response_draft)
        self.db.flush()
        approved = approve_response_draft(
            response_draft.id,
            ResponseDraftReview(),
            self.db,
        )
        self.assertEqual(approved.reviewed_by, INTERNAL_REVIEW_ACTOR)
        self.assertEqual(approved.approval_state, "approved")


if __name__ == "__main__":
    unittest.main()
