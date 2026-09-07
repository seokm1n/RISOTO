"""Leakage, provenance and feature-contract checks for offline story models."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from app.services.story_model import NUMERIC_FEATURES, numeric_features, story_text
from app.training.story_models import freeze_split_plan, split_snapshots, validate_labels


def story(index, day):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    return dict(key=f"1:{index}", company_id=1, company_name="예시기업", story_id=index,
                start_at=start.isoformat(), as_of=(start + timedelta(hours=12)).isoformat(), aliases=[],
                articles=[dict(id=index, title=f"예시기업 사건 {index}", summary="제품 출시",
                               url="https://news.example.com/story", available_at=start.isoformat(),
                               negative_probability=.2)])


class StoryModelTrainingTests(unittest.TestCase):
    def test_split_plan_is_label_independent_and_rejects_changed_snapshots(self):
        rows = [story(i, i) for i in range(30)]
        # A bridge can be uncertain later; it must still prevent cross-split sharing.
        rows[15]["articles"].append(deepcopy(rows[1]["articles"][0]))
        rows[27]["articles"][0]["title"] = rows[15]["articles"][0]["title"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            plan = freeze_split_plan(output, rows)
            self.assertEqual(plan, freeze_split_plan(output, rows))
            self.assertTrue({"1:1", "1:15", "1:27"}.issubset({s["key"] for s in plan["splits"]["purged"]}))
            changed = deepcopy(rows)
            changed[0]["articles"][0]["title"] = "changed evidence"
            with self.assertRaises(ValueError):
                freeze_split_plan(output, changed)

    def test_future_evidence_and_existing_risk_labels_cannot_change_features(self):
        s = story(1, 1)
        changed = deepcopy(s)
        changed.update(y=1, risk_probability=.999, decision="risk")
        future = deepcopy(s["articles"][0])
        future.update(id=999, title="해킹 유출 화재", available_at="2027-01-01T00:00:00+00:00")
        changed["articles"].append(future)
        self.assertEqual(numeric_features(s), numeric_features(changed))
        self.assertEqual(story_text(s), story_text(changed))
        self.assertEqual(len(numeric_features(s)), len(NUMERIC_FEATURES))

    def test_shared_articles_and_titles_never_cross_splits(self):
        rows = [story(i, i) for i in range(30)]
        rows[27]["articles"][0]["title"] = rows[1]["articles"][0]["title"]
        rows[28]["articles"][0]["id"] = rows[2]["articles"][0]["id"]
        splits = split_snapshots(rows)
        self.assertTrue({"1:1", "1:27", "1:2", "1:28"}.issubset({s["key"] for s in splits["purged"]}))
        for left, right in (("train", "validation"), ("validation", "test")):
            self.assertLess(max(s["as_of"] for s in splits[left]), min(s["start_at"] for s in splits[right]))
            self.assertFalse({s["split_group"] for s in splits[left]} & {s["split_group"] for s in splits[right]})

    def test_annotation_requires_matching_evidence_and_one_result_per_key(self):
        s = story(1, 1)
        label = dict(key=s["key"], decision="risk", confidence="high", reason="구체적 근거", evidence_article_ids=[1])
        validate_labels([label], [s])
        for invalid in ([label, label], [{**label, "evidence_article_ids": [999]}], [{**label, "evidence_article_ids": []}]):
            with self.assertRaises(ValueError):
                validate_labels(invalid, [s])


if __name__ == "__main__":
    unittest.main()
