"""Shared pipeline dates constrain counts, pages and evidence at Seoul midnight."""

from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import engine
from app.models import (
    ArticleFilterResult, ArticleRiskAssessment, Company, CompanyArticleMatch,
    NewsArticle, RawNewsArticle, RiskEvent, RiskEventArticle,
    StoryCluster, StoryClusterArticle,
)
from app.routers.collection import (
    _selected_date_bounds, get_filter_summary, list_company_articles,
    list_filter_results, list_risk_events_page, list_risk_judgments_page,
)
from app.routers.operations import list_daily_summaries
from app.services.period_story_cohort import load_period_story_cohort
from tests.auth_helpers import auth_for_company


class PipelineDateRangeTests(unittest.TestCase):
    start_date = date(2024, 1, 2)
    end_date = date(2024, 1, 2)
    start = datetime(2024, 1, 1, 15, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, 15, tzinfo=timezone.utc)

    def setUp(self):
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        self.db = Session(bind=self.connection, expire_on_commit=False)
        owner = self.db.scalar(select(Company).order_by(Company.id).limit(1))
        if owner is None:
            self.db.close()
            self.transaction.rollback()
            self.connection.close()
            self.skipTest("An existing company owner is required.")
        suffix = uuid4().hex
        self.company = Company(
            user_id=owner.user_id, name=f"period-{suffix}",
            normalized_name=f"period-{suffix}", company_role="competitor",
            annual_revenue_krw=1_000_000_000, company_size_class="small_medium",
            monitoring_status="paused", analysis_status="ready",
        )
        self.db.add(self.company)
        self.db.flush()
        self.auth = auth_for_company(self.db, self.company.id)
        self.settings = Settings(story_risk_engine_enabled=True, story_risk_model_enabled=False)
        settings_patch = patch("app.routers.collection.get_settings", return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def tearDown(self):
        self.db.close()
        if self.transaction.is_active:
            self.transaction.rollback()
        self.connection.close()

    def period_args(self):
        return dict(start_date=self.start_date, end_date=self.end_date,
                    db=self.db, auth=self.auth)

    def article(self, timestamp, *, fallback=False, accepted=True):
        suffix = uuid4().hex
        article = NewsArticle(
            source="period-test", title=f"period article {suffix}",
            url=f"https://period.test/{suffix}",
            published_at=None if fallback else timestamp,
            created_at=timestamp,
        )
        self.db.add(article)
        self.db.flush()
        self.db.add(CompanyArticleMatch(company_id=self.company.id, article_id=article.id))
        if accepted:
            raw = RawNewsArticle(
                source=article.source, title=article.title, url=article.url,
                normalized_url=article.url, content_hash=uuid4().hex,
                published_at=article.published_at, collected_at=timestamp,
            )
            self.db.add(raw)
            self.db.flush()
            article.raw_article_id = raw.id
            self.db.add(ArticleFilterResult(
                company_id=self.company.id, raw_article_id=raw.id, curated_article_id=article.id,
                decision="accepted", reason="accepted", classifier_kind="test",
                filter_version="period-fixture", filtered_at=timestamp,
            ))
        return article

    def cluster(self, articles):
        cluster = StoryCluster(
            fingerprint=uuid4().hex, representative_title="Period story",
            first_published_at=self.start, last_published_at=self.end,
        )
        self.db.add(cluster)
        self.db.flush()
        for index, article in enumerate(articles):
            self.db.add(StoryClusterArticle(
                story_cluster_id=cluster.id, article_id=article.id,
                similarity=1.0, is_representative=index == 0,
            ))
        return cluster

    def test_pair_validation_and_inclusive_seoul_calendar_bounds(self):
        self.assertEqual(_selected_date_bounds(self.start_date, self.end_date), (self.start, self.end))
        for start, end in [(self.start_date, None), (None, self.end_date),
                           (date(2024, 1, 3), self.end_date)]:
            with self.subTest(start=start, end=end), self.assertRaises(HTTPException) as caught:
                _selected_date_bounds(start, end)
            self.assertEqual(caught.exception.status_code, 422)

    def test_filter_totals_and_article_pages_share_publication_date_boundaries(self):
        timestamps = [self.start - timedelta(microseconds=1), self.start,
                      self.end - timedelta(microseconds=1), self.end, self.start]
        articles = []
        raws = []
        for index, timestamp in enumerate(timestamps):
            article = self.article(timestamp, fallback=index == 4, accepted=False)
            articles.append(article)
            raw = RawNewsArticle(
                source=article.source, title=article.title, url=article.url,
                normalized_url=article.url, content_hash=uuid4().hex,
                published_at=article.published_at, collected_at=timestamp,
            )
            self.db.add(raw)
            self.db.flush()
            raws.append(raw)
            decision = "review_required" if index == 4 else "rejected"
            self.db.add(ArticleFilterResult(
                raw_article_id=raw.id, company_id=self.company.id,
                decision=decision, reason="irrelevant", classifier_kind="rules_only",
                filter_version="period-v1", filtered_at=self.end + timedelta(days=30),
            ))
        self.db.flush()
        # A later re-review remains the latest decision for an in-period article.
        self.db.add(ArticleFilterResult(
            raw_article_id=raws[1].id, company_id=self.company.id,
            decision="accepted", reason="accepted", classifier_kind="rules_only",
            filter_version="period-v2", filtered_at=self.end + timedelta(days=31),
        ))
        self.db.flush()
        summary = get_filter_summary(self.company.id, **self.period_args())
        results = list_filter_results(self.company.id, decision=None, reason=None,
                                      page=1, page_size=2, **self.period_args())
        second = list_filter_results(self.company.id, decision=None, reason=None,
                                     page=2, page_size=2, **self.period_args())
        self.assertEqual(summary.raw_count, 3)
        self.assertEqual((summary.accepted_count, summary.rejected_count,
                          summary.review_required_count), (1, 1, 1))
        self.assertEqual(results.total, summary.raw_count)
        self.assertEqual({item.raw_article_id for item in results.items + second.items},
                         {raws[index].id for index in (1, 2, 4)})
        self.assertEqual(results.items[0].published_at, self.start)
        page = list_company_articles(
            self.company.id, page=1, page_size=100, source=None, q=None,
            date_from=None, date_to=None, time_from=None, time_to=None,
            days=1, **self.period_args(),
        )
        self.assertEqual(page.total, 3)
        self.assertEqual({item.id for item in page.items},
                         {articles[index].id for index in (1, 2, 4)})

    def test_canonical_filter_totals_match_unique_current_accepted_articles(self):
        accepted = self.article(self.start + timedelta(hours=1))
        fallback = self.article(self.start + timedelta(hours=2), fallback=True)
        rejected = self.article(self.start + timedelta(hours=3))
        self.article(self.start + timedelta(hours=4), accepted=False)
        outside = self.article(self.start - timedelta(days=2))
        different_raw_date = self.article(self.start + timedelta(hours=5))
        self.db.flush()
        self.db.get(RawNewsArticle, outside.raw_article_id).published_at = self.start
        self.db.get(RawNewsArticle, different_raw_date.raw_article_id).published_at = self.start - timedelta(days=2)
        self.db.add(ArticleFilterResult(
            company_id=self.company.id, raw_article_id=rejected.raw_article_id, curated_article_id=rejected.id,
            decision="rejected", reason="irrelevant", classifier_kind="test", filter_version="new-rejected",
            filtered_at=self.start,
        ))
        for decision in ("accepted", "review_required"):
            suffix = uuid4().hex
            raw = RawNewsArticle(source="test", title=f"raw-{suffix}", url=f"https://test/{suffix}",
                                 normalized_url=f"https://test/{suffix}", content_hash=suffix,
                                 published_at=self.start, collected_at=self.start)
            self.db.add(raw)
            self.db.flush()
            self.db.add(ArticleFilterResult(
                company_id=self.company.id, raw_article_id=raw.id,
                curated_article_id=accepted.id if decision == "accepted" else None,
                decision=decision, reason="duplicate" if decision == "accepted" else "irrelevant",
                duplicate_of_raw_id=accepted.raw_article_id if decision == "accepted" else None,
                classifier_kind="test", filter_version="duplicate-current", filtered_at=self.start,
            ))
        self.db.flush()
        summary = get_filter_summary(self.company.id, analysis_only=True, **self.period_args())
        results = list_filter_results(self.company.id, decision="accepted", reason=None,
                                      page=1, page_size=100, analysis_only=True, **self.period_args())
        all_results = list_filter_results(self.company.id, decision=None, reason=None,
                                          page=1, page_size=100, analysis_only=True, **self.period_args())
        articles = list_company_articles(
            self.company.id, page=1, page_size=100, source=None, q=None, date_from=None, date_to=None,
            time_from=None, time_to=None, days=None, analysis_only=True, **self.period_args(),
        )
        expected_ids = {accepted.id, fallback.id, different_raw_date.id}
        self.assertEqual({item.id for item in articles.items}, expected_ids)
        self.assertEqual({item.curated_article_id for item in results.items}, expected_ids)
        self.assertEqual(summary.accepted_count, articles.total)
        self.assertEqual(summary.accepted_count, results.total)
        self.assertEqual(summary.raw_count, summary.accepted_count + summary.rejected_count + summary.review_required_count)
        self.assertEqual(summary.raw_count, all_results.total)
        self.assertEqual((summary.raw_count, summary.raw_record_count), (5, 6))
        self.assertEqual(next(item.title for item in results.items if item.curated_article_id == accepted.id), accepted.title)
        self.assertEqual(next(item.article_date for item in results.items if item.curated_article_id == different_raw_date.id),
                         different_raw_date.published_at)

    def test_tied_timestamps_have_stable_filter_and_article_pages(self):
        articles = [self.article(self.start) for _ in range(9)]
        self.db.flush()
        filter_ids = list(self.db.scalars(
            select(ArticleFilterResult.id).where(ArticleFilterResult.company_id == self.company.id)
            .order_by(ArticleFilterResult.id.desc())
        ))
        filter_pages = [list_filter_results(
            self.company.id, decision="accepted", reason=None, page=page, page_size=2,
            analysis_only=True, **self.period_args(),
        ) for page in range(1, 6)]
        article_pages = [list_company_articles(
            self.company.id, page=page, page_size=2, source=None, q=None, date_from=None, date_to=None,
            time_from=None, time_to=None, days=None, analysis_only=True, **self.period_args(),
        ) for page in range(1, 6)]
        listed_filter_ids = [item.id for page in filter_pages for item in page.items]
        listed_article_ids = [item.id for page in article_pages for item in page.items]
        self.assertEqual(listed_filter_ids, filter_ids)
        self.assertEqual(listed_article_ids, sorted((article.id for article in articles), reverse=True))
        self.assertEqual(len(set(listed_filter_ids)), filter_pages[0].total)
        self.assertEqual(len(set(listed_article_ids)), article_pages[0].total)

    def test_risk_event_summary_pages_and_evidence_use_selected_dates(self):
        articles = [self.article(self.start - timedelta(microseconds=1)),
                    self.article(self.start), self.article(self.end - timedelta(microseconds=1)),
                    self.article(self.end), self.article(self.start, fallback=True)]
        cluster = self.cluster(articles)
        for article in articles[:2]:
            self.db.add(ArticleRiskAssessment(
                company_id=self.company.id, article_id=article.id,
                story_cluster_id=cluster.id, decision="risk", risk_probability=0.95,
                type_scores={}, relevance_score=0.9, source_domain="period.test",
                source_credibility=0.7, classifier_kind="test", model_version="test-v1",
                reason="period test", assessed_at=self.start,
            ))
        timestamps = [self.start - timedelta(microseconds=1), self.start,
                      self.end - timedelta(microseconds=1), self.end, self.start]
        events = []
        for index, timestamp in enumerate(timestamps):
            event = RiskEvent(
                company_id=self.company.id, event_key=f"period-{uuid4().hex}",
                event_source="story_v2", story_cluster_id=cluster.id,
                anomaly_score=0.3, risk_probability=0.8, severity="critical" if index == 1 else "warning",
                status="closed" if index == 2 else "open", response_generation_status="idle",
                opened_at=timestamp, last_seen_at=timestamp,
                last_evidence_at=None if index == 4 else timestamp,
                closed_at=timestamp if index == 2 else None, detected_at=timestamp,
            )
            self.db.add(event)
            self.db.flush()
            events.append(event)
            for article in articles:
                self.db.add(RiskEventArticle(risk_event_id=event.id, article_id=article.id, evidence_score=0.8))
        self.db.flush()
        options = dict(view="all", page=1, page_size=10, days=1,
                       severity=None, risk_type=None, response="all", **self.period_args())
        # The issue is outside the period while its latest article is exactly
        # the next day's midnight, even though older evidence is in range.
        self.assertEqual(list_risk_events_page(self.company.id, **options).total, 0)
        articles[3].published_at = self.end - timedelta(microseconds=1)
        self.db.flush()
        result = list_risk_events_page(self.company.id, **options)
        # One issue counts once even with multiple historical events; membership
        # comes from its period articles, not the chosen event's last update date.
        self.assertEqual(result.total, 1)
        self.assertEqual({item.id for item in result.items}, {events[3].id})
        self.assertEqual((result.summary.active, result.summary.history,
                          result.summary.critical, result.summary.needs_response), (1, 0, 0, 1))
        for item in result.items:
            self.assertEqual(item.evidence_article_count, 5)
            self.assertEqual({row["article_id"] for row in item.evidence_articles},
                             {article.id for article in articles})
            self.assertEqual(item.source_count, 1)
            self.assertEqual(item.risk_article_count, 2)
            self.assertEqual(item.risk_source_count, 1)
            self.assertEqual(item.risk_probability, 0.8)
        judgment = list_risk_judgments_page(
            self.company.id, classification="risk", view="all", page=1,
            page_size=10, days=1, severity=None, risk_type=None, **self.period_args(),
        )
        self.assertEqual(judgment.summary.risk, result.total)
        self.assertEqual(judgment.total, result.total)
        self.assertTrue(all(item.evidence_article_count == 5 for item in judgment.items))
        history = list_risk_events_page(self.company.id, **{**options, "view": "history"})
        self.assertEqual(history.items, [])
        empty = list_risk_events_page(self.company.id, **{
            **options, "start_date": date(2024, 2, 1), "end_date": date(2024, 2, 1),
        })
        self.assertEqual(empty.total, 0)
        self.assertEqual(empty.summary.active + empty.summary.history, 0)
        self.db.add(ArticleFilterResult(
            company_id=self.company.id, raw_article_id=articles[0].raw_article_id,
            curated_article_id=articles[0].id, decision="rejected", reason="irrelevant",
            classifier_kind="test", filter_version="withdrawn", filtered_at=self.start,
        ))
        newly_accepted = self.article(self.start + timedelta(hours=6))
        self.db.add(StoryClusterArticle(story_cluster_id=cluster.id, article_id=newly_accepted.id,
                                       similarity=0.9, is_representative=False))
        self.db.flush()
        updated = list_risk_events_page(self.company.id, **options).items[0]
        expected_ids = {article.id for article in articles[1:]} | {newly_accepted.id}
        self.assertEqual({row["article_id"] for row in updated.evidence_articles}, expected_ids)
        self.assertEqual(updated.evidence_article_count, len(expected_ids))
        self.assertEqual(updated.risk_probability, 0.8)
        self.assertIsNone(next(row["risk_probability"] for row in updated.evidence_articles
                               if row["article_id"] == newly_accepted.id))

    def test_non_risk_summary_and_evidence_match_period_without_changing_score(self):
        clusters = []
        for timestamp in [self.start - timedelta(microseconds=1), self.start,
                          self.end - timedelta(microseconds=1), self.end]:
            articles = [self.article(self.start - timedelta(days=1)), self.article(timestamp), self.article(timestamp)]
            cluster = self.cluster(articles)
            clusters.append(cluster)
            for index, article in enumerate(articles):
                self.db.add(ArticleRiskAssessment(
                    company_id=self.company.id, article_id=article.id,
                    story_cluster_id=cluster.id, decision="non_risk",
                    risk_probability=0.2 if index == 0 else 0.1,
                    type_scores={}, relevance_score=0.9, source_domain="period.test",
                    source_credibility=0.7, classifier_kind="test", model_version="test-v1",
                    reason="period test", assessed_at=self.start,
                ))
        self.db.flush()
        result = list_risk_judgments_page(
            self.company.id, classification="non_risk", view="all", page=1,
            page_size=10, days=1, severity=None, risk_type=None, **self.period_args(),
        )
        self.assertEqual(result.total, 2)
        self.assertEqual(result.summary.non_risk, result.total)
        self.assertEqual({item.story_cluster_id for item in result.items}, {clusters[1].id, clusters[2].id})
        for item in result.items:
            self.assertEqual(item.evidence_article_count, 3)
            self.assertEqual(len(item.evidence_articles), 3)
            self.assertEqual(item.source_count, 1)
            self.assertEqual(item.risk_probability, 0.2)

    def test_grouping_judgments_and_daily_totals_use_the_same_latest_issue_dates(self):
        period = {**self.period_args(), "end_date": date(2024, 1, 3)}
        groups = []
        for index in range(3):
            latest = self.start + timedelta(days=1 if index == 0 else 0, hours=1)
            articles = [self.article(self.start - timedelta(days=3)), self.article(latest)]
            for article in articles:
                article.sentiment_label = "negative" if index == 0 else "positive" if index == 1 else "neutral"
            cluster = self.cluster(articles)
            groups.append((cluster, articles))
            if index == 1:
                for article in articles:
                    self.db.add(ArticleRiskAssessment(
                        company_id=self.company.id, article_id=article.id,
                        story_cluster_id=cluster.id, decision="non_risk", risk_probability=0.2,
                        type_scores={}, relevance_score=0.9, source_domain="period.test",
                        source_credibility=0.7, classifier_kind="test", model_version="test-v1",
                        reason="period test", assessed_at=self.start,
                    ))
        event = RiskEvent(
            company_id=self.company.id, event_key=f"period-{uuid4().hex}",
            event_source="story_v2", story_cluster_id=groups[0][0].id,
            anomaly_score=0.3, risk_probability=0.8, severity="warning", status="open",
            response_generation_status="idle", opened_at=self.start - timedelta(days=10),
            last_evidence_at=self.start - timedelta(days=10), detected_at=self.start,
        )
        self.db.add(event)
        self.db.flush()
        for article in groups[0][1]:
            self.db.add(RiskEventArticle(risk_event_id=event.id, article_id=article.id, evidence_score=0.8))
        self.db.flush()
        cohort = load_period_story_cohort(self.db, self.company.id, self.start_date,
                                         date(2024, 1, 3), settings=self.settings, include_unjudged=False)
        pages = [list_risk_judgments_page(
            self.company.id, classification=classification, view="all", page=1, page_size=10,
            days=None, severity=None, risk_type=None, **period,
        ) for classification in ("risk", "non_risk", "pending")]
        with patch("app.services.period_story_cohort.get_settings", return_value=self.settings):
            daily = list_daily_summaries(self.company.id, **period)
            grouped = list_company_articles(
                self.company.id, page=1, page_size=100, source=None, q=None, date_from=None,
                date_to=None, time_from=None, time_to=None, days=None, period_basis="issues",
                analysis_only=True, judged_only=True, **period,
            )
        self.assertEqual(len({article.story_cluster_id for article in grouped.items}), len(cohort))
        self.assertEqual(len(cohort), sum(page.total for page in pages[:2]))
        self.assertEqual([page.total for page in pages], [1, 1, 1])
        self.assertEqual(sum(day.eligible_story_count for day in daily), 2)
        for key in ("eligible_risk_story_count", "eligible_non_risk_story_count",
                    "eligible_positive_story_count", "eligible_negative_story_count"):
            self.assertEqual(sum(getattr(day, key) for day in daily), 1, key)
        self.assertEqual(sum(day.eligible_pending_story_count for day in daily), 0)
        self.assertEqual(next(day.eligible_risk_story_count for day in daily if day.summary_date == date(2024, 1, 3)), 1)
        self.assertEqual(pages[0].items[0].issue_latest_at, self.start + timedelta(days=1, hours=1))


if __name__ == "__main__":
    unittest.main()
