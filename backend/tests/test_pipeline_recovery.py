"""Pipeline incidents recover only after a successful full realtime execution."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from app.models import Company, NewsArticle
from app.services.monitoring_pipeline import run_collection


class PipelineRecoveryTests(unittest.TestCase):
    def run_scenario(
        self, *, quality="complete", job_type="realtime", failed_stage=None,
        manage_incidents=True, dispatch=True, has_articles=True,
    ):
        """Exercise real orchestration while isolating provider/network/DB effects."""
        now = datetime(2026, 9, 7, 12, 15, tzinfo=timezone.utc)
        events = []
        company = SimpleNamespace(id=77, user_id=88, name="recovery test")
        article = SimpleNamespace(id=20)
        item = SimpleNamespace(
            source="naver_api_hub", title="recovery article", summary="article summary",
            url="https://recovery.test/article", original_url=None, published_at=now,
            raw_payload={}, matched_keyword=None,
        )
        raw = SimpleNamespace(id=10, title=item.title, summary=item.summary, published_at=now)
        collector = SimpleNamespace(source="naver_api_hub", search=Mock(return_value=[item] if has_articles else []))
        db = MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False
        db.get.side_effect = lambda model, _id: company if model is Company else article if model is NewsArticle else None
        db.scalars.return_value = []
        db.scalar.side_effect = [None, SimpleNamespace(decision="accepted", curated_article_id=20)]
        db.commit.side_effect = lambda: events.append("collection_commit")
        recovery_db = MagicMock()
        recovery_db.__enter__.return_value = recovery_db
        recovery_db.__exit__.return_value = False
        recovery_db.commit.side_effect = lambda: events.append("recovery_commit")
        settings = SimpleNamespace(collection_window_minutes=15, story_risk_engine_enabled=True)
        attempts = [SimpleNamespace(source="naver_api_hub", status="succeeded" if quality == "complete" else "failed")]
        mocks = {}
        stage_functions = {
            "analyze_company_articles": "sentiment",
            "enqueue_llm_labeling_for_company": "labeling",
            "enqueue_company_risk_articles": "risk",
            "build_feature_window": "window",
            "close_stale_story_events": "close",
            "recover_company_incidents": "recover",
            "dispatch_pending_notifications": "dispatch",
        }
        def execute_stage(name):
            def execute(*_args, **_kwargs):
                events.append(name)
                if name == failed_stage:
                    raise RuntimeError(f"{name} failed")
            return execute

        with ExitStack() as stack:
            replacements = {
                "get_settings": Mock(return_value=settings),
                "SessionLocal": Mock(side_effect=[db, recovery_db]),
                "_lock_company_collection": Mock(),
                "_lock_normalized_url": Mock(),
                "_collectors": Mock(return_value=([collector], [])),
                "_article_filter_config": Mock(return_value=SimpleNamespace(version="recovery-test")),
                "get_semantic_scorer": Mock(),
                "_raw_for_content": Mock(return_value=raw),
                "assign_story_cluster": Mock(),
                "record_attempts": Mock(return_value=attempts),
                "evaluate_attempts": Mock(return_value=(quality, None)),
                **{function: Mock(side_effect=execute_stage(stage)) for function, stage in stage_functions.items()},
            }
            for function, replacement in replacements.items():
                mocks[function] = stack.enter_context(patch(
                    f"app.services.monitoring_pipeline.{function}", replacement,
                ))
            kwargs = dict(
                company_id=company.id, job_type=job_type,
                requested_from=now - timedelta(minutes=15), requested_to=now,
                sources=["naver_api_hub"], manage_incidents=manage_incidents,
                dispatch_notifications_after=dispatch,
            )
            if failed_stage:
                with self.assertRaisesRegex(RuntimeError, f"{failed_stage} failed"):
                    run_collection(**kwargs)
                job = None
            else:
                job = run_collection(**kwargs)
        return job, mocks, events, recovery_db, settings

    def test_realtime_recovers_after_all_downstream_steps_and_before_notifications(self):
        job, mocks, events, recovery_db, settings = self.run_scenario()
        self.assertEqual(job.status, "completed")
        self.assertEqual(events, ["collection_commit", "sentiment", "labeling", "risk", "window", "close",
                                  "recover", "recovery_commit", "dispatch"])
        mocks["recover_company_incidents"].assert_called_once_with(
            recovery_db, 77, [], settings, pipeline_succeeded=True,
        )
        self.assertEqual(mocks["SessionLocal"].call_count, 2)
        recovery_db.commit.assert_called_once()

    def test_retry_recovers_pipeline_even_with_incident_management_disabled(self):
        job, mocks, events, recovery_db, settings = self.run_scenario(manage_incidents=False, dispatch=False)
        self.assertEqual(job.status, "completed")
        self.assertFalse(mocks["evaluate_attempts"].call_args.kwargs["manage_incidents"])
        mocks["recover_company_incidents"].assert_called_once_with(
            recovery_db, 77, [], settings, pipeline_succeeded=True,
        )
        self.assertEqual(events[-3:], ["close", "recover", "recovery_commit"])
        mocks["dispatch_pending_notifications"].assert_not_called()

    def test_valid_empty_collection_recovers_after_successful_window_processing(self):
        job, mocks, events, _db, _settings = self.run_scenario(has_articles=False)
        self.assertEqual(job.status, "completed")
        mocks["analyze_company_articles"].assert_not_called()
        self.assertEqual(events, ["collection_commit", "window", "close", "recover", "recovery_commit", "dispatch"])

    def test_partial_or_failed_collection_does_not_recover_pipeline(self):
        for quality, status in (("partial", "partial"), ("unavailable", "failed")):
            with self.subTest(quality=quality):
                job, mocks, _events, recovery_db, _settings = self.run_scenario(quality=quality)
                self.assertEqual(job.status, status)
                mocks["recover_company_incidents"].assert_not_called()
                recovery_db.commit.assert_not_called()
                self.assertEqual(mocks["SessionLocal"].call_count, 1)

    def test_downstream_failure_does_not_recover_or_dispatch(self):
        for stage in ("sentiment", "labeling", "risk", "window", "close"):
            with self.subTest(stage=stage):
                _job, mocks, _events, recovery_db, _settings = self.run_scenario(failed_stage=stage)
                mocks["recover_company_incidents"].assert_not_called()
                mocks["dispatch_pending_notifications"].assert_not_called()
                recovery_db.commit.assert_not_called()
                self.assertEqual(mocks["SessionLocal"].call_count, 1)

    def test_manual_or_backfill_success_does_not_recover_realtime_pipeline(self):
        for job_type in ("manual", "backfill"):
            with self.subTest(job_type=job_type):
                job, mocks, _events, recovery_db, _settings = self.run_scenario(job_type=job_type)
                self.assertEqual(job.status, "completed")
                mocks["recover_company_incidents"].assert_not_called()
                recovery_db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
