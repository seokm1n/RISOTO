"""Read-only status contract for the active story model, with no database writes."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.routers.governance import get_model_runtime_status, get_risk_detection_status


class StoryRiskStatusTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            story_risk_engine_enabled=True,
            story_risk_model_enabled=True,
            pretrained_relevance_model_path="/no/advertising/model",
            pretrained_sentiment_model_path="/no/sentiment/model",
            external_lightgbm_model_path="/no/window/model.txt",
            article_filter_version="test-filter",
            article_filter_ai_enabled=True,
        )
        self.story = SimpleNamespace(
            available=True, version="story-if-lgbm-full-v2-test",
            model_state="provisional", reason=None,
            message="스토리 Isolation Forest + LightGBM 적용 중. AI 라벨 학습이며 사람 검증 전입니다.",
            threshold=0.26, artifact_sha256="a" * 64,
        )
        self.db = Mock()
        self.db.scalar.return_value = None
        self.settings_patch = patch("app.routers.governance.get_settings", return_value=self.settings)
        self.story_patch = patch("app.routers.governance.resolve_story_risk_runtime", return_value=self.story)
        self.window_patch = patch("app.routers.governance.resolve_production_risk_detector")
        self.settings_patch.start()
        self.story_resolver = self.story_patch.start()
        self.window_resolver = self.window_patch.start()
        self.addCleanup(patch.stopall)

    def test_story_model_reports_actual_version_and_provisional_provenance(self):
        status = get_risk_detection_status(self.db)

        self.assertEqual(status.scoring_scope, "story")
        self.assertEqual(status.risk_detection_status, "available")
        self.assertEqual(status.model_state, "provisional")
        self.assertEqual(status.model_version, self.story.version)
        self.assertIsNone(status.model_id)
        self.assertEqual(status.threshold, 0.26)
        self.assertEqual(status.artifact_sha256, "a" * 64)
        self.assertIn("AI 라벨", status.message)
        self.window_resolver.assert_not_called()
        self.db.scalar.assert_not_called()

    def test_unavailable_story_does_not_fall_back_to_window_model(self):
        for reason in ("model_not_configured", "artifact_missing", "artifact_hash_mismatch", "artifact_invalid"):
            with self.subTest(reason=reason):
                self.story.available = False
                self.story.reason = reason
                self.story.message = "스토리 모델 파일을 확인해야 합니다."
                status = get_risk_detection_status(self.db)
                self.assertEqual(status.risk_detection_status, "unavailable")
                self.assertEqual(status.model_state, "unavailable")
                self.assertEqual(status.scoring_scope, "story")
                self.assertEqual(status.reason, reason)
                self.assertEqual(status.message, self.story.message)
        self.window_resolver.assert_not_called()

    def test_disabling_either_story_flag_preserves_window_status(self):
        self.window_resolver.return_value = SimpleNamespace(
            available=True,
            version=SimpleNamespace(id=7, version="window-v1", thresholds={"model_state": "provisional"}),
        )
        for flag in ("story_risk_model_enabled", "story_risk_engine_enabled"):
            with self.subTest(flag=flag):
                setattr(self.settings, flag, False)
                status = get_risk_detection_status(self.db)
                self.assertEqual(status.scoring_scope, "window")
                self.assertEqual(status.model_id, 7)
                self.assertEqual(status.model_version, "window-v1")
                self.assertEqual(status.model_state, "provisional")
                setattr(self.settings, flag, True)
        self.story_resolver.assert_not_called()

    def test_window_unavailable_preserves_existing_reason_contract(self):
        self.settings.story_risk_model_enabled = False
        self.window_resolver.return_value = SimpleNamespace(
            available=False, reason="production_lightgbm_not_registered", version=None,
        )
        status = get_risk_detection_status(self.db)
        self.assertEqual(status.risk_detection_status, "unavailable")
        self.assertEqual(status.reason, "production_lightgbm_not_registered")
        self.assertEqual(status.scoring_scope, "window")

    def test_runtime_status_keeps_external_window_model_separate(self):
        with TemporaryDirectory() as directory:
            window_model = Path(directory) / "window-model.txt"
            window_model.write_text("test fixture", encoding="utf-8")
            self.settings.external_lightgbm_model_path = str(window_model)
            status = get_model_runtime_status(self.db)

        self.assertEqual(status.scoring_scope, "story")
        self.assertTrue(status.risk_model_available)
        self.assertEqual(status.risk_model_name, self.story.version)
        self.assertEqual(status.risk_model_state, "provisional")
        self.assertEqual(status.risk_model_message, self.story.message)
        self.assertTrue(status.external_lightgbm_model_available)
        self.assertIn("15분 구간용", status.external_lightgbm_message)
        self.assertIn("별도 스토리 모델", status.external_lightgbm_message)
        self.window_resolver.assert_not_called()
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
