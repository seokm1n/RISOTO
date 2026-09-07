"""Serving contract, frozen evidence and fail-closed artifact verification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from lightgbm import LGBMClassifier
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import RobustScaler

from app.services.story_model import NUMERIC_FEATURES, SCHEMA_VERSION, numeric_features, story_text, transform_stories
from app.services.story_model_runtime import (
    _load_runtime, build_story_snapshot, resolve_story_risk_runtime, snapshot_digest,
)


START = datetime(2026, 9, 1, tzinfo=timezone.utc)


def article(index, hours=0, **overrides):
    values = dict(id=index, title=f"기업 제품 {index}", summary="정상 소식", created_at=START + timedelta(hours=hours),
                  published_at=START - timedelta(days=1), analyzed_at=START, negative_probability=.2,
                  url="https://news.example.com", original_url=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def snapshot(index=1):
    return build_story_snapshot(1, "기업", [], [article(index)], story_id=index, as_of=START + timedelta(days=2))


class StoryModelRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "model.joblib"
        rows = [snapshot(i) for i in range(24)]
        for index, row in enumerate(rows):
            row["articles"][0]["title"] = f"기업 {'화재 사고 피해' if index % 2 else '신제품 출시'} {index}"
            row["snapshot_hash"] = snapshot_digest(row)
        numeric = np.asarray([numeric_features(s) for s in rows])
        scaler = RobustScaler().fit(numeric)
        isolation = IsolationForest(n_estimators=3, random_state=1).fit(scaler.transform(numeric))
        tfidf = TfidfVectorizer(analyzer="char", ngram_range=(2, 3)).fit([story_text(s) for s in rows])
        svd = TruncatedSVD(n_components=2, random_state=1).fit(tfidf.transform([story_text(s) for s in rows]))
        cls.bundle = dict(schema_version=SCHEMA_VERSION, numeric_features=NUMERIC_FEATURES,
                          version="test-story-v1", scaler=scaler, isolation_forest=isolation,
                          tfidf=tfidf, svd=svd, if_reference_scores=np.sort(-isolation.decision_function(scaler.transform(numeric))),
                          threshold=.5)
        matrix = transform_stories(cls.bundle, rows)
        cls.bundle["lightgbm"] = LGBMClassifier(n_estimators=3, min_child_samples=2, verbosity=-1, n_jobs=1).fit(
            matrix, [i % 2 for i in range(len(rows))])
        joblib.dump(cls.bundle, cls.path)
        cls.settings = SimpleNamespace(story_risk_model_enabled=True, story_risk_model_path=str(cls.path),
                                       story_risk_model_sha256=hashlib.sha256(cls.path.read_bytes()).hexdigest())

    @classmethod
    def tearDownClass(cls):
        _load_runtime.cache_clear()
        cls.directory.cleanup()

    def test_frozen_evidence_matches_training_and_excludes_future_sentiment(self):
        items = [article(i, hours=i) for i in range(12)]
        items[0].summary = "x" * 1500
        items[0].original_url = "https://original.example.com"
        items[1].analyzed_at = START + timedelta(hours=25)
        items.append(article(99, hours=25))
        row = build_story_snapshot(1, "기업", ["별칭"], reversed(items), story_id=4, as_of=START + timedelta(days=2))
        self.assertEqual(row["key"], "1:4")
        self.assertEqual([a["id"] for a in row["articles"]], list(range(8)))
        self.assertEqual(row["as_of"], (START + timedelta(days=1)).isoformat())
        self.assertEqual(len(row["articles"][0]["summary"]), 1000)
        self.assertEqual(row["articles"][0]["url"], "https://original.example.com")
        self.assertIsNone(row["articles"][1]["negative_probability"])
        self.assertEqual(row["snapshot_hash"], snapshot_digest(row))
        repeated = build_story_snapshot(1, "기업", ["별칭"], items, story_id=4, as_of=START + timedelta(days=20))
        self.assertEqual(row, repeated)

    def test_early_cutoff_uses_created_at_and_stable_id_order(self):
        items = [article(3), article(2), article(1, hours=3), article(4, hours=25)]
        row = build_story_snapshot(1, "기업", [], items, story_id=1, as_of=START + timedelta(hours=2))
        self.assertEqual([a["id"] for a in row["articles"]], [2, 3])
        self.assertEqual(row["as_of"], (START + timedelta(hours=2)).isoformat())
        with self.assertRaises(ValueError):
            build_story_snapshot(1, "기업", [], [article(1, hours=3)], story_id=1, as_of=START)

    def test_unavailable_does_not_issue_normal_or_fallback_prediction(self):
        runtime = resolve_story_risk_runtime(SimpleNamespace(story_risk_model_enabled=False))
        result = runtime.predict([snapshot()])[0]
        self.assertFalse(runtime.available)
        self.assertEqual(result["reason"], "model_disabled")
        self.assertIsNone(result["is_risk"])
        self.assertIsNone(result["risk_probability"])

    def test_hash_is_checked_before_deserialization(self):
        settings = SimpleNamespace(**vars(self.settings))
        settings.story_risk_model_sha256 = "0" * 64
        with patch("joblib.load", side_effect=AssertionError("Untrusted load attempted")):
            runtime = resolve_story_risk_runtime(settings)
        self.assertFalse(runtime.available)
        self.assertEqual(runtime.reason, "artifact_hash_mismatch")

    def test_incompatible_artifact_fails_closed(self):
        invalid_path = Path(self.directory.name) / "invalid.joblib"
        joblib.dump({**self.bundle, "numeric_features": list(reversed(NUMERIC_FEATURES))}, invalid_path)
        settings = SimpleNamespace(story_risk_model_enabled=True, story_risk_model_path=str(invalid_path),
                                   story_risk_model_sha256=hashlib.sha256(invalid_path.read_bytes()).hexdigest())
        runtime = resolve_story_risk_runtime(settings)
        self.assertFalse(runtime.available)
        self.assertEqual(runtime.reason, "artifact_invalid")

    def test_cached_runtime_matches_offline_predictions_and_records_provenance(self):
        runtime = resolve_story_risk_runtime(self.settings)
        self.assertTrue(runtime.available, runtime.message)
        self.assertIs(runtime, resolve_story_risk_runtime(self.settings))
        rows = [snapshot(1), snapshot(2)]
        result = runtime.predict(rows)
        matrix = transform_stories(self.bundle, rows)
        expected = self.bundle["lightgbm"].predict_proba(matrix)[:, 1]
        np.testing.assert_allclose([r["risk_probability"] for r in result], expected)
        np.testing.assert_allclose([r["anomaly_score"] for r in result], matrix[:, -2])
        self.assertEqual(result[0]["artifact_sha256"], self.settings.story_risk_model_sha256)
        self.assertEqual(result[0]["model_state"], "provisional")
        self.assertEqual(result[0]["snapshot_hash"], rows[0]["snapshot_hash"])
        self.assertEqual(runtime.predict([]), [])

    def test_changed_snapshot_and_invalid_probabilities_are_not_scored(self):
        runtime = resolve_story_risk_runtime(self.settings)
        changed = deepcopy(snapshot())
        changed["articles"][0]["title"] = "tampered"
        self.assertEqual(runtime.predict([changed])[0]["reason"], "prediction_failed")
        with patch.object(runtime._bundle["lightgbm"], "predict_proba", return_value=np.array([[np.nan, .5]])):
            result = runtime.predict([snapshot()])[0]
        self.assertFalse(result["available"])
        self.assertIsNone(result["is_risk"])


if __name__ == "__main__":
    unittest.main()
