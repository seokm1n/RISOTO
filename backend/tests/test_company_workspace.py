"""Company roles, user ownership, and financial-input contracts."""

from decimal import Decimal
import unittest
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import ArticleFilterResult, Company, Industry, RawNewsArticle, User
from app.routers.companies import (
    create_main_company,
    create_or_update_company,
    delete_company,
)
from app.schemas import CompanyCreate
from tests.auth_helpers import auth_for_user


class CompanyRevenueSchemaTests(unittest.TestCase):
    def test_revenue_is_positive_plain_decimal_with_at_most_two_places(self):
        base = {
            "name": "재무 검증 기업",
            "industry_id": 1,
            "company_size_class": "small_medium",
        }
        self.assertEqual(
            CompanyCreate(**base, annual_revenue_100m_krw="123.45").annual_revenue_100m_krw,
            Decimal("123.45"),
        )
        for invalid in ("0", "-1", "1.001", "1e3", "abc"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                CompanyCreate(**base, annual_revenue_100m_krw=invalid)
        with self.assertRaises(ValidationError):
            CompanyCreate(
                **{key: value for key, value in base.items() if key != "company_size_class"},
                annual_revenue_100m_krw="1",
                company_size_class="startup",
            )


class CompanyRoleDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            self.industry_id = self.db.scalar(select(Industry.id).order_by(Industry.id).limit(1))
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if self.industry_id is None:
            self.skipTest("기업 역할 테스트에 산업군이 필요합니다.")

        suffix = uuid4().hex
        user = User(email=f"company-{suffix}@example.com", password_hash="unused")
        self.db.add(user)
        self.db.flush()
        self.auth = auth_for_user(self.db, user.id)

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def _payload(self, name: str, revenue: str = "123.45") -> CompanyCreate:
        return CompanyCreate(
            name=name,
            industry_id=self.industry_id,
            annual_revenue_100m_krw=revenue,
            company_size_class="mid_sized",
            keywords=[],
        )

    def test_one_undeletable_main_and_competitor_only_regular_registration(self):
        main = create_main_company(
            self._payload("메인 재무 기업"),
            BackgroundTasks(),
            self.db,
            self.auth,
        )
        stored_main = self.db.get(Company, main.id)
        self.assertEqual(stored_main.company_role, "main")
        self.assertEqual(stored_main.annual_revenue_krw, 12_345_000_000)
        self.assertEqual(stored_main.monitoring_status, "active")
        self.assertEqual(main.readiness_status, "active")

        with self.assertRaises(HTTPException) as duplicate_main:
            create_main_company(
                self._payload("두 번째 메인"),
                BackgroundTasks(),
                self.db,
                self.auth,
            )
        self.assertEqual(duplicate_main.exception.status_code, 409)

        with self.assertRaises(HTTPException) as delete_main:
            delete_company(main.id, self.db, self.auth)
        self.assertEqual(delete_main.exception.status_code, 409)

        competitor = create_or_update_company(
            self._payload("추가 등록 기업", "10.01"),
            BackgroundTasks(),
            self.db,
            self.auth,
        )
        stored_competitor = self.db.get(Company, competitor.id)
        self.assertEqual(stored_competitor.company_role, "competitor")
        self.assertEqual(stored_competitor.annual_revenue_krw, 1_001_000_000)
        self.assertEqual(stored_competitor.monitoring_status, "active")
        self.assertEqual(competitor.readiness_status, "active")

    def test_delete_competitor_preserves_raw_article_used_as_duplicate_target(self):
        suffix = uuid4().hex
        main = create_main_company(
            self._payload(f"삭제 회귀 메인 {suffix}"),
            BackgroundTasks(),
            self.db,
            self.auth,
        )
        competitor = create_or_update_company(
            self._payload(f"삭제 회귀 경쟁사 {suffix}"),
            BackgroundTasks(),
            self.db,
            self.auth,
        )
        canonical_url = f"https://example.com/{suffix}/canonical"
        duplicate_url = f"https://example.com/{suffix}/duplicate"
        canonical_raw = RawNewsArticle(
            source="test",
            title="중복 판정 기준 기사",
            url=canonical_url,
            normalized_url=canonical_url,
            content_hash=f"canonical-{suffix}",
            raw_payload={},
        )
        duplicate_raw = RawNewsArticle(
            source="test",
            title="중복으로 판정된 기사",
            url=duplicate_url,
            normalized_url=duplicate_url,
            content_hash=f"duplicate-{suffix}",
            raw_payload={},
        )
        self.db.add_all([canonical_raw, duplicate_raw])
        self.db.flush()
        duplicate_result = ArticleFilterResult(
            raw_article_id=duplicate_raw.id,
            company_id=main.id,
            decision="rejected",
            reason="duplicate",
            duplicate_of_raw_id=canonical_raw.id,
            relevance_score=0.9,
            advertising_score=0.0,
            confidence=1.0,
            classifier_kind="test",
            filter_version=f"delete-regression-{suffix}",
            details={},
        )
        self.db.add(duplicate_result)
        self.db.commit()
        canonical_raw_id = canonical_raw.id
        duplicate_result_id = duplicate_result.id

        response = delete_company(competitor.id, self.db, self.auth)

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.db.get(Company, competitor.id))
        self.assertIsNotNone(self.db.get(RawNewsArticle, canonical_raw_id))
        stored_result = self.db.get(ArticleFilterResult, duplicate_result_id)
        self.assertIsNotNone(stored_result)
        self.assertEqual(stored_result.duplicate_of_raw_id, canonical_raw_id)


if __name__ == "__main__":
    unittest.main()
