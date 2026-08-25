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
from app.services.risk_analysis import (
    BASE_FEATURE_NAMES,
    REQUIRED_IF_HASH_KEY,
    REQUIRED_IF_VERSION_KEY,
    RISK_DETECTOR_FEATURE_NAMES,
    artifact_sha256,
)


class StubLightGbm:
    """Minimal trusted test artifact satisfying the serving interface."""

    def predict_proba(self, values):
        return np.asarray([[0.25, 0.75] for _ in values], dtype=float)


class StubIsolationForest:
    def decision_function(self, values):
        return np.zeros(len(values), dtype=float)


class RiskDetectionStatusDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.artifact_path: Path | None = None
        self.isolation_artifact_path: Path | None = None
        self.db = SessionLocal()
        self.transaction = self.db.begin()
        try:
            production = list(
                self.db.scalars(
                    select(ModelVersion).where(
                        ModelVersion.task.in_(["risk_detector", "isolation_forest"]),
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
        if self.isolation_artifact_path is not None:
            self.isolation_artifact_path.unlink(missing_ok=True)

    def test_no_production_lightgbm_is_explicitly_unavailable(self):
        status = get_risk_detection_status(self.db)

        self.assertEqual(status.risk_detection_status, "unavailable")
        self.assertEqual(status.reason, "production_lightgbm_not_registered")
        self.assertEqual(status.model_state, "unavailable")
        self.assertIsNone(status.model_id)
        self.assertIsNone(status.model_version)
        self.assertIn("위험 판정을 수행하지 않습니다", status.message)

    def test_valid_production_artifact_exposes_version_and_provisional_state(self):
        isolation_handle = tempfile.NamedTemporaryFile(
            prefix="risoto-risk-status-if-",
            suffix=".joblib",
            delete=False,
        )
        isolation_handle.close()
        self.isolation_artifact_path = Path(isolation_handle.name)
        joblib.dump(
            {
                "model": StubIsolationForest(),
                "feature_names": BASE_FEATURE_NAMES,
                "company_scalers": {
                    "global": {
                        "center": [0.0] * len(BASE_FEATURE_NAMES),
                        "scale": [1.0] * len(BASE_FEATURE_NAMES),
                    }
                },
            },
            self.isolation_artifact_path,
        )
        isolation_hash = artifact_sha256(self.isolation_artifact_path)
        self.assertIsNotNone(isolation_hash)
        isolation_version = f"external-if-{uuid4().hex}"
        self.db.add(
            ModelVersion(
                task="isolation_forest",
                version=isolation_version,
                status="production",
                artifact_path=str(self.isolation_artifact_path),
                training_data_hash="b" * 64,
                label_schema={},
                metrics={},
                thresholds={},
                training_counts={},
                dependencies={},
                promoted_at=datetime.now(timezone.utc),
            )
        )

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
                REQUIRED_IF_VERSION_KEY: isolation_version,
                REQUIRED_IF_HASH_KEY: isolation_hash,
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
            dependencies={
                REQUIRED_IF_VERSION_KEY: isolation_version,
                REQUIRED_IF_HASH_KEY: isolation_hash,
            },
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
