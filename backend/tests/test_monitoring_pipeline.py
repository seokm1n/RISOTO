"""수집 파이프라인의 정제 기사 재사용과 중복 연결 회귀 테스트."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.database import SessionLocal, engine
from app.models import CollectionJob, NewsArticle, RawNewsArticle
from app.services.article_filtering import FilterDecision
from app.services.monitoring_pipeline import (
    _curated_for_raw_or_url,
    _get_or_create_curated_article,
    _incident_retry_sources,
    _raw_for_content,
    _reuse_existing_curated_article,
    build_queries,
    completed_window_start,
    query_kind_for,
    run_collection,
    run_due_collection_retries,
)


class RawArticleVersionTests(unittest.TestCase):
    """동일 URL의 내용 변경이 기존 원문 기록을 덮어쓰지 않는지 검증한다."""

    def test_raw_lookup_uses_source_url_and_content_hash(self):
        """원문 유일키의 세 필드를 모두 조회 조건으로 사용한다."""
        existing = SimpleNamespace(id=12357)
        db = SimpleNamespace(scalar=Mock(return_value=existing))

        result = _raw_for_content(
            db,
            "kakao_daum",
            "https://example.com/list?a=1&b=2",
            "content-version-2",
        )

        self.assertIs(result, existing)
        statement = db.scalar.call_args.args[0]
        where_clause = str(statement.whereclause)
        self.assertIn("raw_news_articles.source", where_clause)
        self.assertIn("raw_news_articles.normalized_url", where_clause)
        self.assertIn("raw_news_articles.content_hash", where_clause)
        self.assertTrue(
            {
                "kakao_daum",
                "https://example.com/list?a=1&b=2",
                "content-version-2",
            }.issubset(set(statement.compile().params.values()))
        )


class ExistingCuratedArticleTests(unittest.TestCase):
    """기존 정제 기사 연결이 원문 자기 참조 제약을 위반하지 않는지 검증한다."""

    @staticmethod
    def decision() -> FilterDecision:
        """정제 통과 상태의 최소 필터 판정 객체를 만든다."""
        return FilterDecision(
            decision="accepted",
            reason="accepted",
            relevance_score=0.9,
            advertising_score=0.0,
            confidence=0.9,
            classifier_kind="rules_only",
            filter_version="test",
        )

    def test_same_raw_reuse_does_not_mark_self_duplicate(self):
        """같은 원문의 정제 기사를 재사용할 때 자기 자신을 중복 대상으로 기록하지 않는다."""
        decision = self.decision()
        _reuse_existing_curated_article(
            decision,
            SimpleNamespace(id=12),
            SimpleNamespace(raw_article_id=12),
        )
        self.assertEqual((decision.decision, decision.reason), ("accepted", "accepted"))
        self.assertIsNone(decision.duplicate_of_raw_id)
        self.assertTrue(decision.details["existing_curated_reused"])

    def test_other_raw_reuse_records_canonical_duplicate(self):
        """다른 원문의 정제 URL을 재사용하면 해당 원문을 중복 기준으로 연결한다."""
        decision = self.decision()
        _reuse_existing_curated_article(
            decision,
            SimpleNamespace(id=12),
            SimpleNamespace(raw_article_id=8),
        )
        self.assertEqual((decision.decision, decision.reason), ("accepted", "duplicate"))
        self.assertEqual(decision.duplicate_of_raw_id, 8)
        self.assertEqual(decision.details["duplicate_evidence"], "existing_curated_url")

    @patch("app.services.monitoring_pipeline._curated_for_raw")
    def test_same_raw_is_reused_before_a_changed_normalized_url_lookup(self, curated_for_raw):
        """과거 URL 표준형이 달라도 동일 raw_article_id의 정제 기사를 다시 INSERT하지 않는다."""
        existing = SimpleNamespace(
            id=9116,
            raw_article_id=12357,
            url="https://example.com/article?srt=3&srd=1",
        )
        curated_for_raw.return_value = existing
        db = SimpleNamespace(scalar=Mock())

        result = _curated_for_raw_or_url(
            db,
            12357,
            "https://example.com/article?srd=1&srt=3",
        )

        self.assertIs(result, existing)
        db.scalar.assert_not_called()

    @patch("app.services.monitoring_pipeline._curated_for_raw")
    def test_url_lookup_remains_the_fallback_for_a_different_raw(self, curated_for_raw):
        """동일 원문 정제 기사가 없을 때는 기존 정규 URL 재사용 경로를 유지한다."""
        existing = SimpleNamespace(id=22, raw_article_id=8)
        curated_for_raw.return_value = None
        db = SimpleNamespace(scalar=Mock(return_value=existing))

        result = _curated_for_raw_or_url(
            db,
            12,
            "https://example.com/article",
        )

        self.assertIs(result, existing)
        db.scalar.assert_called_once()


@unittest.skipUnless(
    engine.dialect.name == "postgresql",
    "PostgreSQL advisory-lock concurrency test",
)
class CuratedArticleConcurrencyTests(unittest.TestCase):
    """Concurrent retries must converge on one curated row, not a unique-key error."""

    def test_concurrent_creation_for_one_raw_article_is_idempotent(self):
        marker = uuid4().hex
        url = f"https://example.test/race/{marker}?a=1&b=2"
        with SessionLocal() as db:
            raw = RawNewsArticle(
                source="concurrency_test",
                title=f"race {marker}",
                summary="one raw article",
                url=url,
                original_url=url,
                normalized_url=url,
                content_hash=marker.ljust(64, "0")[:64],
                published_at=datetime.now(timezone.utc),
                raw_payload={},
            )
            db.add(raw)
            db.commit()
            raw_id = raw.id

        item = SimpleNamespace(
            source="concurrency_test",
            title=f"race {marker}",
            summary="one raw article",
            url=url,
            original_url=url,
            published_at=datetime.now(timezone.utc),
            raw_payload={},
        )
        barrier = Barrier(2)

        def create_from_worker() -> tuple[int, bool]:
            with SessionLocal() as db:
                worker_raw = db.get(RawNewsArticle, raw_id)
                barrier.wait(timeout=5)
                article, created = _get_or_create_curated_article(
                    db,
                    worker_raw,
                    item,
                    url,
                )
                db.commit()
                return article.id, created

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: create_from_worker(), range(2)))

            self.assertEqual(len({article_id for article_id, _ in results}), 1)
            self.assertEqual(sum(created for _, created in results), 1)
            with SessionLocal() as db:
                count = db.scalar(
                    select(func.count(NewsArticle.id)).where(
                        NewsArticle.raw_article_id == raw_id
                    )
                )
                self.assertEqual(count, 1)
        finally:
            with SessionLocal() as db:
                db.execute(
                    delete(NewsArticle).where(NewsArticle.raw_article_id == raw_id)
                )
                db.execute(delete(RawNewsArticle).where(RawNewsArticle.id == raw_id))
                db.commit()


class QueryBuildingTests(unittest.TestCase):
    """기업명 결합 검색과 키워드 단독 검색이 함께 생성되는지 검증한다."""

    def test_keyword_only_queries_are_included(self):
        company = SimpleNamespace(name="예시기업")
        keywords = [
            SimpleNamespace(keyword_type="product", value="예시 서비스"),
            SimpleNamespace(keyword_type="risk", value="개인정보 유출"),
        ]

        queries = build_queries(company, keywords, limit=10)

        self.assertEqual(
            queries,
            [
                "예시기업",
                '"예시기업" 예시 서비스',
                "예시 서비스",
                '"예시기업" 개인정보 유출',
                "개인정보 유출",
            ],
        )

    def test_competitor_terms_are_not_collection_queries_and_hit_kinds_are_explicit(self):
        company = SimpleNamespace(name="예시기업")
        keywords = [
            SimpleNamespace(keyword_type="alias", value="예시"),
            SimpleNamespace(keyword_type="competitor", value="경쟁사"),
            SimpleNamespace(keyword_type="product", value="예시몰"),
            SimpleNamespace(keyword_type="risk", value="정보 유출"),
        ]
        queries = build_queries(company, keywords, limit=20)
        self.assertFalse(any("경쟁사" in query for query in queries))
        self.assertEqual(query_kind_for("예시", company, keywords), "alias")
        self.assertEqual(query_kind_for('"예시기업" 예시몰', company, keywords), "product")
        self.assertEqual(query_kind_for("정보 유출", company, keywords), "risk")

    def test_internal_collection_does_not_drop_late_risk_keywords(self):
        company = SimpleNamespace(name="예시기업")
        keywords = [
            SimpleNamespace(keyword_type="alias", value=f"별칭{index}")
            for index in range(6)
        ] + [SimpleNamespace(keyword_type="risk", value="개인정보 유출")]

        queries = build_queries(company, keywords)

        self.assertIn("개인정보 유출", queries)
        self.assertGreater(len(queries), 10)

    def test_completed_window_is_the_interval_that_just_ended(self):
        value = datetime(2026, 8, 20, 0, 15, 7, tzinfo=timezone.utc)
        self.assertEqual(
            completed_window_start(value, 15),
            datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )


class CollectionJobOwnerTests(unittest.TestCase):
    """수집 작업이 회사와 같은 사용자 소유로 저장되는지 검증한다."""

    def test_run_collection_copies_company_owner_to_job(self):
        """파이프라인이 만든 작업은 대상 회사의 user_id를 이어받는다."""
        class StopAfterJobFlush(Exception):
            pass

        class FakeSession:
            def __init__(self):
                self.added = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, _model, _company_id):
                return SimpleNamespace(id=19, user_id=73, name="예시기업")

            def scalars(self, _query):
                return []

            def add(self, item):
                self.added.append(item)

            def flush(self):
                raise StopAfterJobFlush

        db = FakeSession()
        settings = SimpleNamespace(collection_window_minutes=15)
        requested_from = datetime(2026, 8, 25, tzinfo=timezone.utc)
        requested_to = datetime(2026, 8, 25, 1, tzinfo=timezone.utc)

        with patch(
            "app.services.monitoring_pipeline.SessionLocal",
            return_value=db,
        ), patch(
            "app.services.monitoring_pipeline.get_settings",
            return_value=settings,
        ), self.assertRaises(StopAfterJobFlush):
            run_collection(
                19,
                "manual",
                requested_from,
                requested_to=requested_to,
                sources=["naver_api_hub"],
            )

        job = next(item for item in db.added if isinstance(item, CollectionJob))
        self.assertEqual(job.user_id, 73)
        self.assertEqual(job.company_id, 19)


class CollectionRetryTests(unittest.TestCase):
    """수집 장애 재시도가 실제 제공자를 다시 호출하는지 검증한다."""

    def test_pipeline_source_is_replaced_with_configured_collectors(self):
        settings = SimpleNamespace()
        with patch(
            "app.services.monitoring_pipeline._realtime_sources",
            return_value=["naver_api_hub", "kakao_daum"],
        ):
            sources = _incident_retry_sources(settings, ["pipeline"])

        self.assertEqual(sources, ["naver_api_hub", "kakao_daum"])

    def test_due_pipeline_incident_passes_real_sources_to_collection(self):
        scheduled_for = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
        incident = SimpleNamespace(
            id=27,
            user_id=73,
            sources=["pipeline"],
            scheduled_for=scheduled_for,
            affected_company_ids=[14],
            retry_count=1,
        )
        settings = SimpleNamespace(realtime_overlap_minutes=5)

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def scalars(self, _query):
                return [incident]

            def get(self, _model, company_id):
                return SimpleNamespace(id=company_id, user_id=73)

        with patch(
            "app.services.monitoring_pipeline.SessionLocal",
            return_value=FakeSession(),
        ), patch(
            "app.services.monitoring_pipeline.get_settings",
            return_value=settings,
        ), patch(
            "app.services.monitoring_pipeline._realtime_sources",
            return_value=["kakao_daum"],
        ), patch(
            "app.services.monitoring_pipeline.run_collection",
            return_value=SimpleNamespace(status="completed"),
        ) as run_collection, patch(
            "app.services.monitoring_pipeline.complete_retry",
        ) as complete_retry, patch(
            "app.services.monitoring_pipeline.dispatch_pending_notifications",
        ):
            retried = run_due_collection_retries()

        self.assertEqual(retried, 1)
        self.assertEqual(run_collection.call_args.kwargs["sources"], ["kakao_daum"])
        complete_retry.assert_called_once_with(27, [14], settings)

    def test_retry_skips_cross_user_and_dangling_company_ids(self):
        scheduled_for = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
        incident = SimpleNamespace(
            id=28,
            user_id=73,
            sources=["pipeline"],
            scheduled_for=scheduled_for,
            affected_company_ids=[15, 999],
            retry_count=1,
        )
        settings = SimpleNamespace(realtime_overlap_minutes=5)

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def scalars(self, _query):
                return [incident]

            def get(self, _model, company_id):
                if company_id == 15:
                    return SimpleNamespace(id=15, user_id=88)
                return None

        with patch(
            "app.services.monitoring_pipeline.SessionLocal",
            return_value=FakeSession(),
        ), patch(
            "app.services.monitoring_pipeline.get_settings",
            return_value=settings,
        ), patch(
            "app.services.monitoring_pipeline._realtime_sources",
            return_value=["kakao_daum"],
        ), patch(
            "app.services.monitoring_pipeline.run_collection",
        ) as run_collection, patch(
            "app.services.monitoring_pipeline.complete_retry",
        ) as complete_retry, patch(
            "app.services.monitoring_pipeline.dispatch_pending_notifications",
        ):
            retried = run_due_collection_retries()

        self.assertEqual(retried, 0)
        run_collection.assert_not_called()
        complete_retry.assert_called_once_with(28, [15, 999], settings)


if __name__ == "__main__":
    unittest.main()
