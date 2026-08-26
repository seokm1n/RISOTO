"""Automatic LLM article labeling: candidate selection, writes and audit sampling."""

from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import ArticleLabel, Company
from app.services.llm_labeling import (
    _unlabeled_query,
    audit_sample_candidates,
    call_llm_label,
    label_articles,
    llm_labeling_status,
)
from app.services.review_identity import INTERNAL_REVIEW_ACTOR


FAKE_PAYLOAD = {
    "relevance_label": "relevant",
    "advertisement_label": "no",
    "sentiment_label": "neutral",
    "reason": "test payload",
}


class LlmLabelingUnitTests(unittest.TestCase):
    def test_call_llm_label_returns_none_without_an_api_key(self):
        class _EmptySettings:
            openai_api_key = ""

        with patch("app.services.llm_labeling.get_settings", return_value=_EmptySettings()):
            result = call_llm_label({"name": "테스트"}, object(), "gpt-test")
        self.assertIsNone(result)


class LlmLabelingDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            self.company_id = self.db.scalar(select(Company.id).order_by(Company.id).limit(1))
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.company_id is None:
            self.skipTest("LLM 라벨링 테스트에 기업이 필요합니다.")

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def _unlabeled_candidate(self):
        """Use the same query label_articles uses, so the test picks its actual first candidate."""
        row = self.db.execute(_unlabeled_query(None, 1)).first()
        if row is None:
            return None
        _result, raw, company = row
        return SimpleNamespace(company_id=company.id, raw_article_id=raw.id)

    def test_label_articles_writes_a_confirmed_llm_label_and_is_idempotent(self):
        candidate = self._unlabeled_candidate()
        if candidate is None:
            self.skipTest("라벨이 전혀 없는 기사 후보가 없습니다.")

        with patch(
            "app.services.llm_labeling.call_llm_label", return_value=dict(FAKE_PAYLOAD)
        ):
            result = label_articles(self.db, company_id=None, limit=1)

        label = self.db.scalar(
            select(ArticleLabel).where(
                ArticleLabel.company_id == candidate.company_id,
                ArticleLabel.raw_article_id == candidate.raw_article_id,
            )
        )
        self.assertIsNotNone(label)
        self.assertTrue(label.annotator.startswith("llm:"))
        self.assertEqual(label.status, "confirmed")
        self.assertEqual(label.relevance_label, "relevant")
        self.assertEqual(result["labeled"], 1)

        # The same article must not be re-selected as a candidate once confirmed.
        next_candidate = self._unlabeled_candidate()
        if next_candidate is not None:
            self.assertNotEqual(
                (next_candidate.company_id, next_candidate.raw_article_id),
                (candidate.company_id, candidate.raw_article_id),
            )

    def test_label_articles_counts_failures_without_writing_a_row(self):
        candidate = self._unlabeled_candidate()
        if candidate is None:
            self.skipTest("라벨이 전혀 없는 기사 후보가 없습니다.")

        with patch("app.services.llm_labeling.call_llm_label", return_value=None):
            result = label_articles(self.db, company_id=candidate.company_id, limit=50)

        self.assertGreaterEqual(result["failed"], 1)
        label = self.db.scalar(
            select(ArticleLabel).where(
                ArticleLabel.company_id == candidate.company_id,
                ArticleLabel.raw_article_id == candidate.raw_article_id,
            )
        )
        self.assertIsNone(label)

    def test_audit_sample_excludes_articles_once_a_human_has_cross_checked_them(self):
        candidate = self._unlabeled_candidate()
        if candidate is None:
            self.skipTest("라벨이 전혀 없는 기사 후보가 없습니다.")
        user_id = self.db.scalar(select(Company.user_id).where(Company.id == candidate.company_id))

        self.db.add(
            ArticleLabel(
                company_id=candidate.company_id,
                raw_article_id=candidate.raw_article_id,
                annotator=f"llm:test-{uuid4().hex}",
                relevance_label="relevant",
                advertisement_label="no",
                sentiment_label="neutral",
                status="confirmed",
            )
        )
        self.db.flush()

        sample = audit_sample_candidates(self.db, user_id, 100)
        self.assertTrue(
            any(
                result.company_id == candidate.company_id
                and result.raw_article_id == candidate.raw_article_id
                for result, _raw, _company in sample
            )
        )

        self.db.add(
            ArticleLabel(
                company_id=candidate.company_id,
                raw_article_id=candidate.raw_article_id,
                annotator=INTERNAL_REVIEW_ACTOR,
                relevance_label="relevant",
                advertisement_label="no",
                sentiment_label="neutral",
                status="confirmed",
            )
        )
        self.db.flush()

        sample_after_audit = audit_sample_candidates(self.db, user_id, 100)
        self.assertFalse(
            any(
                result.company_id == candidate.company_id
                and result.raw_article_id == candidate.raw_article_id
                for result, _raw, _company in sample_after_audit
            )
        )

    def test_status_agreement_rate_reflects_matching_human_and_llm_labels(self):
        candidate = self._unlabeled_candidate()
        if candidate is None:
            self.skipTest("라벨이 전혀 없는 기사 후보가 없습니다.")
        now = datetime.now(timezone.utc)

        self.db.add(
            ArticleLabel(
                company_id=candidate.company_id,
                raw_article_id=candidate.raw_article_id,
                annotator=f"llm:test-{uuid4().hex}",
                relevance_label="relevant",
                advertisement_label="no",
                sentiment_label="positive",
                status="confirmed",
                reviewed_at=now - timedelta(hours=1),
            )
        )
        self.db.add(
            ArticleLabel(
                company_id=candidate.company_id,
                raw_article_id=candidate.raw_article_id,
                annotator=INTERNAL_REVIEW_ACTOR,
                relevance_label="relevant",
                advertisement_label="no",
                sentiment_label="negative",
                status="confirmed",
                reviewed_at=now,
            )
        )
        self.db.flush()

        status = llm_labeling_status(self.db, now=now)

        self.assertGreaterEqual(status["audit"]["reviewed_count"], 1)
        self.assertLess(status["audit"]["agreement_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
