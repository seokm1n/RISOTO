"""운영 대응방안 경로가 v3 엔진을 가리키는지 확인한다."""

from __future__ import annotations

import inspect
import unittest

from app.routers import governance
from app.services import risk_analysis
from app.services.response_engine import generate_response_draft


class ResponseEngineWiringTests(unittest.TestCase):
    def test_manual_generation_uses_v3_engine(self):
        self.assertIs(governance.generate_response_draft, generate_response_draft)

    def test_automatic_generation_uses_v3_engine(self):
        source = inspect.getsource(risk_analysis.build_feature_window)
        self.assertIn(
            "from app.services.response_engine import enqueue_response_draft",
            source,
        )


if __name__ == "__main__":
    unittest.main()
