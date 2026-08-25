"""Production LightGBM availability contract and read-only status API tests."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import joblib
import numpy as np
from sqlalchemy import select

from app.database import SessionLocal
from app.models import ModelVersion
from app.routers.governance import get_risk_detection_status
from app.services.risk_analysis import RISK_DETECTOR_FEATURE_NAMES


class StubLightGbm:
    """Minimal trusted test artifact satisfying the serving interface."""

    def predict_proba(self, values):
        return np.asarray([[0.25, 0.75] for _ in values], dtype=float)


class RiskDetectionStatusDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.artifact_path: Path | None = None
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            production = list(
                self.db.scalars(
                    select(ModelVersion).where(
                        ModelVersion.task == "risk_detector",
                        ModelVersion.status == "production",
                    )
                )
            )
        except Exception as exc:
            self.db.close()
            self.skipTest(f"PostgreSQL 테스트 연결이 없습니다: {exc}")
        for model in production:
            model.status = "retired"
            model.retired_at = datetime.now(timezone.utc)
        self.db.flush()

    def tearDown(self):
        if hasattr(self, "transaction") and self.transaction.is_active:
            self.transaction.rollback()
        if hasattr(self, "db"):
            self.db.close()
        if self.artifact_path is not None:
            self.artifact_path.unlink(missing_ok=True)

    def test_no_production_lightgbm_is_explicitly_unavailable(self):
        status = get_risk_detection_status(self.db)

        self.assertEqual(status.risk_detection_status, "unavailable")
        self.assertEqual(status.reason, "production_lightgbm_not_registered")
        self.assertEqual(status.model_state, "unavailable")
        self.assertIsNone(status.model_id)
        self.assertIsNone(status.model_version)
        self.assertIn("위험 판정을 수행하지 않습니다", status.message)

    def test_valid_production_artifact_exposes_version_and_provisional_state(self):
        handle = tempfile.NamedTemporaryFile(
            prefix="risoto-risk-status-",
            suffix=".joblib",
            delete=False,
        )
        handle.close()
        self.artifact_path = Path(handle.name)
        joblib.dump(
            {
                "model": StubLightGbm(),
                "feature_names": RISK_DETECTOR_FEATURE_NAMES,
            },
            self.artifact_path,
        )
        version = f"external-risk-{uuid4().hex}"
        model = ModelVersion(
            task="risk_detector",
            version=version,
            status="production",
            artifact_path=str(self.artifact_path),
            training_data_hash="a" * 64,
            label_schema={"target": {"normal": 0, "risk": 1}},
            metrics={},
            thresholds={
                "global": 0.65,
                "per_company": {},
                "model_state": "provisional",
            },
            training_counts={},
            promoted_at=datetime.now(timezone.utc),
        )
        self.db.add(model)
        self.db.flush()

        status = get_risk_detection_status(self.db)

        self.assertEqual(status.risk_detection_status, "available")
        self.assertIsNone(status.reason)
        self.assertEqual(status.model_id, model.id)
        self.assertEqual(status.model_version, version)
        self.assertEqual(status.model_state, "provisional")


if __name__ == "__main__":
    unittest.main()
