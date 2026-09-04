"""위험관리 페이지네이션과 비동기 개별 생성 API 계약."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    ArticleRiskAssessment,
    Company,
    CompanyArticleMatch,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
    StoryCluster,
    StoryClusterArticle,
)
from app.routers.collection import list_risk_events_page, list_risk_judgments_page
from app.routers.governance import start_response_generation
from tests.auth_helpers import auth_for_company


class RiskEventPageDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        owner_company = self.db.scalar(select(Company).order_by(Company.id).limit(1))
        if owner_company is None:
            self.db.close()
            self.skipTest("위험관리 API 테스트에 회원 한 명이 필요합니다.")
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        self.company = Company(
            user_id=owner_company.user_id,
            name=f"risk-page-test-{suffix}",
            normalized_name=f"risk-page-test-{suffix}",
            company_role="competitor",
            annual_revenue_krw=1_000_000_000,
            company_size_class="small_medium",
            monitoring_status="active",
            analysis_status="ready",
        )
        self.db.add(self.company)
        self.db.commit()
        self.auth = auth_for_company(self.db, self.company.id)
        self.cluster = StoryCluster(
            fingerprint=uuid4().hex,
            representative_title="위험관리 페이지 테스트 스토리",
            first_published_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            last_published_at=datetime(2099, 1, 2, tzinfo=timezone.utc),
        )
        self.db.add(self.cluster)
        self.db.flush()
        self.evidence_articles = []
        for index in range(2):
            article = NewsArticle(
                source="risk-page-test",
                title=f"위험관리 근거 기사 {index}",
                url=f"https://risk-page.test/{self.company.id}/{uuid4().hex}",
                published_at=datetime(2099, 1, 1, tzinfo=timezone.utc)
                + timedelta(minutes=index),
            )
            self.db.add(article)
            self.db.flush()
            self.evidence_articles.append(article)

    def tearDown(self):
        if hasattr(self, "company"):
            company = self.db.get(Company, self.company.id)
            if company is not None:
                self.db.delete(company)
                self.db.commit()
        if hasattr(self, "evidence_articles"):
            for article in self.evidence_articles:
                stored = self.db.get(NewsArticle, article.id)
                if stored is not None:
                    self.db.delete(stored)
        if hasattr(self, "cluster"):
            stored_cluster = self.db.get(StoryCluster, self.cluster.id)
            if stored_cluster is not None:
                self.db.delete(stored_cluster)
        self.db.commit()
        self.db.close()

    def event(
        self,
        index: int,
        *,
        status: str,
        severity: str,
        response_status: str,
        evidence_count: int = 2,
        event_source: str = "story_v2",
    ) -> RiskEvent:
        timestamp = datetime(2099, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        event = RiskEvent(
            company_id=self.company.id,
            event_key=f"story-v3:{self.company.id}:page-test-{index}",
            event_source=event_source,
            story_cluster_id=self.cluster.id if event_source == "story_v2" else None,
            anomaly_score=0.5,
            risk_probability=0.7 + index / 100,
            severity=severity,
            status=status,
            primary_type="security_privacy",
            summary=f"스토리 사건 {index}",
            model_state="provisional",
            approval_state="draft",
            response_generation_status=response_status,
            opened_at=timestamp,
            last_seen_at=timestamp,
            last_evidence_at=timestamp,
            closed_at=timestamp if status == "closed" else None,
            detected_at=timestamp,
        )
        self.db.add(event)
        self.db.flush()
        self.db.add(RiskEventType(
            risk_event_id=event.id,
            risk_type="security_privacy",
            probability=0.9,
            is_primary=True,
            evidence={"source": "test"},
        ))
        for article in self.evidence_articles[:evidence_count]:
            self.db.add(RiskEventArticle(
                risk_event_id=event.id,
                article_id=article.id,
                evidence_score=0.8,
            ))
        return event

    def test_page_filters_summary_and_fixed_sorting(self):
        open_event = self.event(1, status="open", severity="warning", response_status="deferred")
        generating = self.event(2, status="monitoring", severity="warning", response_status="generating")
        critical = self.event(3, status="acknowledged", severity="critical", response_status="generated")
        closed = self.event(4, status="closed", severity="warning", response_status="generated")
        self.event(5, status="legacy_candidate", severity="critical", response_status="failed")
        self.event(6, status="dismissed", severity="critical", response_status="failed")
        singleton = self.event(7, status="open", severity="critical", response_status="deferred", evidence_count=1)
        window_event = self.event(8, status="open", severity="critical", response_status="deferred", event_source="window_v1")
        self.db.commit()

        result = list_risk_events_page(
            self.company.id,
            view="active",
            page=1,
            page_size=2,
            days=None,
            severity=None,
            risk_type=None,
            response="all",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual(result.total, 3)
        self.assertEqual([item.id for item in result.items], [critical.id, generating.id])
        self.assertEqual(result.summary.active, 3)
        self.assertEqual(result.summary.critical, 1)
        self.assertEqual(result.summary.needs_response, 1)
        self.assertEqual(result.summary.history, 1)
        self.assertNotIn(singleton.id, [item.id for item in result.items])
        self.assertNotIn(window_event.id, [item.id for item in result.items])

        all_events = list_risk_events_page(
            self.company.id,
            view="all",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type=None,
            response="all",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual(
            [item.id for item in all_events.items],
            [closed.id, critical.id, generating.id, open_event.id],
        )

        needs_response = list_risk_events_page(
            self.company.id,
            view="active",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type="security_privacy",
            response="needs_action",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual([item.id for item in needs_response.items], [open_event.id])

        without_needs_response = list_risk_events_page(
            self.company.id,
            view="active",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type=None,
            response="without_needs_action",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual([item.id for item in without_needs_response.items], [critical.id, generating.id])

        history = list_risk_events_page(
            self.company.id,
            view="history",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type=None,
            response="all",
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual([item.id for item in history.items], [closed.id])

    def test_active_idle_event_requires_response_but_closed_idle_remains_in_history(self):
        active_idle = self.event(1, status="open", severity="warning", response_status="idle")
        closed_idle = self.event(2, status="closed", severity="warning", response_status="idle")
        self.db.commit()

        needs_response = list_risk_events_page(
            self.company.id, view="active", page=1, page_size=10, days=None,
            severity=None, risk_type=None, response="needs_action", db=self.db, auth=self.auth,
        )
        active_without_needs = list_risk_events_page(
            self.company.id, view="active", page=1, page_size=10, days=None,
            severity=None, risk_type=None, response="without_needs_action", db=self.db, auth=self.auth,
        )
        history = list_risk_events_page(
            self.company.id, view="history", page=1, page_size=10, days=None,
            severity=None, risk_type=None, response="all", db=self.db, auth=self.auth,
        )

        self.assertIn(active_idle.id, [item.id for item in needs_response.items])
        self.assertNotIn(active_idle.id, [item.id for item in active_without_needs.items])
        self.assertIn(closed_idle.id, [item.id for item in history.items])

    def test_active_response_review_respects_period_filter(self):
        recent = self.event(1, status="open", severity="warning", response_status="idle")
        stale = self.event(2, status="open", severity="warning", response_status="failed")
        stale.opened_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        stale.last_seen_at = stale.opened_at
        stale.last_evidence_at = stale.opened_at
        stale.detected_at = stale.opened_at
        self.db.commit()

        result = list_risk_events_page(
            self.company.id, view="active", page=1, page_size=10, days=1,
            severity=None, risk_type=None, response="needs_action", db=self.db, auth=self.auth,
        )

        self.assertEqual([item.id for item in result.items], [recent.id])

    def test_risk_judgments_split_eligible_stories_into_risk_and_non_risk(self):
        for index, article in enumerate(self.evidence_articles):
            self.db.add(CompanyArticleMatch(
                company_id=self.company.id,
                article_id=article.id,
            ))
            self.db.add(StoryClusterArticle(
                article_id=article.id,
                story_cluster_id=self.cluster.id,
                similarity=1.0 - index / 10,
                is_representative=index == 0,
            ))
            self.db.add(ArticleRiskAssessment(
                company_id=self.company.id,
                article_id=article.id,
                story_cluster_id=self.cluster.id,
                decision="non_risk",
                risk_probability=0.20 + index / 10,
                type_scores={},
                primary_type=None,
                relevance_score=0.9,
                source_domain="risk-page.test",
                source_credibility=0.65,
                classifier_kind="test",
                model_version="test-v1",
                reason="test non-risk judgment",
                assessed_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            ))
        self.db.commit()

        non_risk = list_risk_judgments_page(
            self.company.id,
            classification="non_risk",
            view="active",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type=None,
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual(non_risk.summary.risk, 0)
        self.assertEqual(non_risk.summary.non_risk, 1)
        self.assertEqual(non_risk.total, 1)
        self.assertEqual(non_risk.items[0].classification, "non_risk")
        self.assertEqual(non_risk.items[0].evidence_article_count, 2)
        self.assertEqual(non_risk.items[0].source_count, 1)

        for article in self.evidence_articles:
            article.published_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.db.commit()
        recent_non_risk = list_risk_judgments_page(
            self.company.id,
            classification="non_risk",
            view="all",
            page=1,
            page_size=10,
            days=1,
            severity=None,
            risk_type=None,
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual(recent_non_risk.summary.non_risk, 1)
        self.assertEqual(recent_non_risk.total, 0)

        event = self.event(
            1,
            status="open",
            severity="warning",
            response_status="generated",
        )
        self.db.commit()
        risk = list_risk_judgments_page(
            self.company.id,
            classification="risk",
            view="active",
            page=1,
            page_size=10,
            days=None,
            severity=None,
            risk_type=None,
            db=self.db,
            auth=self.auth,
        )
        self.assertEqual(risk.summary.risk, 1)
        self.assertEqual(risk.summary.non_risk, 0)
        self.assertEqual([item.risk_event_id for item in risk.items], [event.id])

    @patch("app.routers.governance.enqueue_response_draft")
    def test_response_generation_is_idempotent_while_pending(self, enqueue):
        event = self.event(1, status="open", severity="warning", response_status="deferred")
        self.db.commit()

        first = start_response_generation(event.id, force=False, db=self.db, auth=self.auth)
        second = start_response_generation(event.id, force=False, db=self.db, auth=self.auth)

        self.assertEqual(first.status, "pending")
        self.assertEqual(second.status, "pending")
        enqueue.assert_called_once_with(event.id, force=False)

    @patch("app.routers.governance.enqueue_response_draft")
    def test_response_generation_force_restarts_stuck_job(self, enqueue):
        event = self.event(1, status="open", severity="warning", response_status="generating")
        self.db.commit()

        result = start_response_generation(event.id, force=True, db=self.db, auth=self.auth)

        self.assertEqual(result.status, "pending")
        enqueue.assert_called_once_with(event.id, force=True)


if __name__ == "__main__":
    unittest.main()
