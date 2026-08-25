"""Collection control state transitions."""

from types import SimpleNamespace
import unittest

from app.routers.collection import _resumed_monitoring_status


class CollectionControlTests(unittest.TestCase):
    def test_resume_preserves_human_activation(self):
        self.assertEqual(
            _resumed_monitoring_status(SimpleNamespace(analysis_status="ready")),
            "active",
        )

    def test_resume_keeps_unapproved_company_warming(self):
        self.assertEqual(
            _resumed_monitoring_status(SimpleNamespace(analysis_status="warming")),
            "warming",
        )


if __name__ == "__main__":
    unittest.main()
