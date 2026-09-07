"""Binary LLM filter review persistence and downstream article linking."""

import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    ArticleFilterResult,
    Company,
    CompanyArticleMatch,
    NewsArticle,
    RawNewsArticle,
)
from app.services.monitoring_pipeline import apply_binary_filter_review


class BinaryFilterReviewDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            self.company = self.db.scalar(select(Company).order_by(Company.id).limit(1))
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.company is None:
            self.skipTest("LLM 정제 재검토 테스트에 기업이 필요합니다.")

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def _review_required_result(self) -> tuple[RawNewsArticle, ArticleFilterResult]:
        suffix = uuid4().hex
        raw = RawNewsArticle(
            source="test",
            title=f"LLM 재검토 테스트 {suffix}",
            summary="대상 기업과 관련된 테스트 기사",
            url=f"https://example.com/{suffix}",
            original_url=f"https://example.com/{suffix}",
            normalized_url=f"https://example.com/{suffix}",
            content_hash=suffix,
            raw_payload={},
        )
        self.db.add(raw)
        self.db.flush()
        result = ArticleFilterResult(
            raw_article_id=raw.id,
            company_id=self.company.id,
            decision="review_required",
            reason="irrelevant",
            relevance_score=0.5,
            advertising_score=0.2,
            confidence=0.5,
            classifier_kind="rules_only",
            filter_version=f"test-{suffix}",
            details={},
        )
        self.db.add(result)
        self.db.flush()
        return raw, result

    @staticmethod
    def _review(decision: str, reason: str) -> dict:
        return {
            "decision": decision,
            "reason": reason,
            "relevance_score": 0.94 if decision == "accepted" else 0.12,
            "advertising_score": 0.03,
            "confidence": 0.91,
            "explanation": "테스트 재검토 근거",
            "provider": "openai",
            "model_name": "gpt-test",
        }

    def test_accepted_review_creates_latest_result_and_company_match(self):
        raw, source = self._review_required_result()

        reviewed, article_id = apply_binary_filter_review(
            self.db,
            self.company,
            source,
            raw,
            self._review("accepted", "accepted"),
        )

        self.assertEqual(reviewed.decision, "accepted")
        self.assertNotEqual(reviewed.id, source.id)
        self.assertIsNotNone(article_id)
        self.assertIsNotNone(self.db.get(NewsArticle, article_id))
        self.assertIsNotNone(self.db.get(CompanyArticleMatch, (self.company.id, article_id)))
        self.assertEqual(reviewed.details["llm_review"]["ambiguous_policy"], "rejected")
        latest = self.db.scalar(
            select(ArticleFilterResult)
            .where(
                ArticleFilterResult.company_id == self.company.id,
                ArticleFilterResult.raw_article_id == raw.id,
            )
            .order_by(ArticleFilterResult.id.desc())
            .limit(1)
        )
        self.assertEqual(latest.id, reviewed.id)

    def test_rejected_review_never_materializes_an_article(self):
        raw, source = self._review_required_result()

        reviewed, article_id = apply_binary_filter_review(
            self.db,
            self.company,
            source,
            raw,
            self._review("rejected", "irrelevant"),
        )

        self.assertEqual(reviewed.decision, "rejected")
        self.assertIsNone(article_id)
        self.assertIsNone(
            self.db.scalar(select(NewsArticle).where(NewsArticle.raw_article_id == raw.id))
        )


if __name__ == "__main__":
    unittest.main()
