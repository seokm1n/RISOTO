"""Company-aware reranker preprocessing and leakage-control tests."""

import unittest

from app.services.company_reranker import (
    build_article_passage,
    build_company_query,
    strip_affiliate_boilerplate,
)
from app.training.company_reranker import calibrate_thresholds, company_holdout_split


class CompanyRerankerTests(unittest.TestCase):
    def test_dynamic_query_supports_a_new_company(self):
        query = build_company_query("새로운테크", ["뉴테크"], ["로보픽"])
        self.assertIn("대상 기업: 새로운테크", query)
        self.assertIn("뉴테크", query)
        self.assertIn("로보픽", query)
        self.assertIn("물류·사업장", query)

    def test_affiliate_disclosure_is_removed_from_passage(self):
        cleaned, found = strip_affiliate_boilerplate(
            "이 포스팅은 쿠팡 파트너스 활동의 일환으로, "
            "이에 따른 일정액의 수수료를 제공받습니다."
        )
        self.assertTrue(found)
        self.assertNotIn("쿠팡", cleaned)
        self.assertNotIn("쿠팡 파트너스", build_article_passage("여행 후기", cleaned))

    def test_company_holdout_has_no_company_leakage(self):
        rows = [
            {"company_group": company, "label": label}
            for company in ("a", "b", "c", "d", "e", "f")
            for label in (0, 1)
        ]
        splits = company_holdout_split(rows)
        companies = {
            name: {row["company_group"] for row in items}
            for name, items in splits.items()
        }
        self.assertFalse(companies["train"] & companies["validation"])
        self.assertFalse(companies["train"] & companies["test"])
        self.assertFalse(companies["validation"] & companies["test"])

    def test_threshold_calibration_creates_review_band(self):
        thresholds = calibrate_thresholds(
            [0, 0, 0, 1, 1, 1],
            [0.02, 0.08, 0.20, 0.72, 0.85, 0.96],
        )
        self.assertLess(thresholds["reject"], thresholds["accept"])


if __name__ == "__main__":
    unittest.main()

