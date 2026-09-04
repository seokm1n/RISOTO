"""Daily graph counts each company story only at its first qualifying moment."""

from datetime import datetime, timedelta
import unittest
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    Company,
    CompanyArticleMatch,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    StoryCluster,
    StoryClusterArticle,
)
from app.routers.operations import list_daily_summaries
from tests.auth_helpers import auth_for_company


SEOUL = ZoneInfo("Asia/Seoul")


class DailyStorySummaryDatabaseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.connection = engine.connect()
            self.transaction = self.connection.begin()
            self.db = Session(bind=self.connection, expire_on_commit=False)
            owner = self.db.scalar(select(Company).order_by(Company.id).limit(1))
        except Exception as exc:
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        if owner is None:
            self.skipTest("일일 스토리 집계 테스트에 회원이 필요합니다.")
        suffix = uuid4().hex
        self.company = Company(
            user_id=owner.user_id,
            name=f"daily-story-{suffix}",
            normalized_name=f"daily-story-{suffix}",
            company_role="competitor",
            annual_revenue_krw=1_000_000_000,
            company_size_class="small_medium",
            monitoring_status="active",
            analysis_status="ready",
        )
        self.db.add(self.company)
        self.db.flush()
        self.auth = auth_for_company(self.db, self.company.id)

    def tearDown(self):
        if hasattr(self, "db"):
            self.db.close()
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "connection"):
            self.connection.close()

    def _article(
        self,
        cluster: StoryCluster,
        published_at: datetime,
        index: int,
        probabilities: tuple[float, float, float],
    ) -> NewsArticle:
        positive, neutral, negative = probabilities
        article = NewsArticle(
            source="daily-story-test",
            title=f"스토리 집계 기사 {index}",
            url=f"https://daily-story.test/{uuid4().hex}",
            published_at=published_at,
            sentiment_label=(
                "positive"
                if positive > max(neutral, negative)
                else "negative" if negative > neutral else "neutral"
            ),
            positive_probability=positive,
            neutral_probability=neutral,
            negative_probability=negative,
        )
        self.db.add(article)
        self.db.flush()
        self.db.add_all([
            StoryClusterArticle(
                article_id=article.id,
                story_cluster_id=cluster.id,
                similarity=1.0 if index == 1 else 0.9,
                is_representative=index == 1,
            ),
            CompanyArticleMatch(
                company_id=self.company.id,
                article_id=article.id,
                matched_keyword="test",
            ),
        ])
        return article

    def test_follow_up_article_updates_story_without_incrementing_daily_story_count(self):
        now = datetime.now(SEOUL)
        yesterday = now - timedelta(days=1)
        first_cluster = StoryCluster(
            fingerprint=uuid4().hex,
            representative_title="어제 시작해 오늘 후속 기사가 붙은 스토리",
            first_published_at=yesterday,
            last_published_at=now,
        )
        second_cluster = StoryCluster(
            fingerprint=uuid4().hex,
            representative_title="오늘 처음 추가된 단일 기사 스토리",
            first_published_at=now,
            last_published_at=now,
        )
        self.db.add_all([first_cluster, second_cluster])
        self.db.flush()
        first = self._article(first_cluster, yesterday, 1, (0.90, 0.05, 0.05))
        # A negative follow-up is present, but the story-level probability
        # average remains positive: positive=.50, neutral=.125, negative=.375.
        follow_up = self._article(first_cluster, now, 2, (0.10, 0.20, 0.70))
        singleton = self._article(second_cluster, now, 3, (0.05, 0.10, 0.85))

        eligible_event = RiskEvent(
            company_id=self.company.id,
            article_id=first.id,
            story_cluster_id=first_cluster.id,
            event_key=f"story-v3:{self.company.id}:{first_cluster.id}",
            event_source="story_v2",
            anomaly_score=0.7,
            risk_probability=0.9,
            severity="critical",
            status="open",
            primary_type="reputation_consumer",
            summary=first_cluster.representative_title,
            model_state="provisional",
            approval_state="draft",
            opened_at=now,
            last_seen_at=now,
            last_evidence_at=now,
        )
        singleton_event = RiskEvent(
            company_id=self.company.id,
            article_id=singleton.id,
            story_cluster_id=second_cluster.id,
            event_key=f"story-v3:{self.company.id}:{second_cluster.id}",
            event_source="story_v2",
            anomaly_score=0.8,
            risk_probability=0.95,
            severity="critical",
            status="open",
            primary_type="reputation_consumer",
            summary=second_cluster.representative_title,
            model_state="provisional",
            approval_state="draft",
            opened_at=now,
            last_seen_at=now,
            last_evidence_at=now,
        )
        self.db.add_all([eligible_event, singleton_event])
        self.db.flush()
        self.db.add_all([
            RiskEventArticle(risk_event_id=eligible_event.id, article_id=first.id),
            RiskEventArticle(risk_event_id=eligible_event.id, article_id=follow_up.id),
            RiskEventArticle(risk_event_id=singleton_event.id, article_id=singleton.id),
        ])
        self.db.flush()

        summaries = list_daily_summaries(
            self.company.id,
            days=2,
            db=self.db,
            auth=self.auth,
        )
        by_date = {item.summary_date: item for item in summaries}

        self.assertEqual(by_date[yesterday.date()].story_count, 1)
        self.assertEqual(by_date[now.date()].story_count, 1)
        self.assertEqual(by_date[yesterday.date()].negative_story_count, 0)
        self.assertEqual(by_date[now.date()].negative_story_count, 1)
        self.assertEqual(by_date[now.date()].risk_event_count, 1)
        self.assertEqual(by_date[yesterday.date()].eligible_story_count, 0)
        self.assertEqual(by_date[now.date()].eligible_story_count, 1)
        self.assertEqual(by_date[now.date()].eligible_positive_story_count, 1)
        self.assertEqual(by_date[now.date()].eligible_neutral_story_count, 0)
        self.assertEqual(by_date[now.date()].eligible_negative_story_count, 0)
        self.assertEqual(by_date[now.date()].eligible_risk_story_count, 1)

    def test_negative_story_enters_comparison_cohort_on_second_article_day(self):
        now = datetime.now(SEOUL)
        yesterday = now - timedelta(days=1)
        cluster = StoryCluster(
            fingerprint=uuid4().hex,
            representative_title="두 번째 기사에서 비교 대상이 된 부정 스토리",
            first_published_at=yesterday,
            last_published_at=now,
        )
        self.db.add(cluster)
        self.db.flush()
        self._article(cluster, yesterday, 1, (0.05, 0.10, 0.85))
        self._article(cluster, now, 2, (0.10, 0.15, 0.75))
        self.db.flush()

        summaries = list_daily_summaries(
            self.company.id,
            days=2,
            db=self.db,
            auth=self.auth,
        )
        by_date = {item.summary_date: item for item in summaries}

        self.assertEqual(by_date[yesterday.date()].eligible_story_count, 0)
        self.assertEqual(by_date[now.date()].eligible_story_count, 1)
        self.assertEqual(by_date[now.date()].eligible_positive_story_count, 0)
        self.assertEqual(by_date[now.date()].eligible_neutral_story_count, 0)
        self.assertEqual(by_date[now.date()].eligible_negative_story_count, 1)
        self.assertEqual(by_date[now.date()].eligible_risk_story_count, 0)


if __name__ == "__main__":
    unittest.main()
