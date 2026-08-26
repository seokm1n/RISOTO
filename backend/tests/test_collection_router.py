"""Collection control state transitions."""

from types import SimpleNamespace
import unittest

from app.routers.collection import _resumed_monitoring_status


class CollectionControlTests(unittest.TestCase):
    def test_resume_ready_company_is_active(self):
        self.assertEqual(
            _resumed_monitoring_status(SimpleNamespace(analysis_status="ready")),
            "active",
        )

    def test_resume_warming_company_is_immediately_active(self):
        self.assertEqual(
            _resumed_monitoring_status(SimpleNamespace(analysis_status="warming")),
            "active",
        )


if __name__ == "__main__":
    unittest.main()
