"""Evidence-exposure checks without model loading, training, or network access."""
from copy import deepcopy
import unittest

from app.training.story_model_comparison import exposure_filter, index_rows, split_keys


def snapshot(key, story_id, articles):
    return dict(key=key, story_id=story_id,
                articles=[dict(id=article_id, title=title) for article_id, title in articles])


def manifest(*, train=(), validation=(), test=(), purged=()):
    return {split: [dict(key=key) for key in keys] for split, keys in
            (("train", train), ("validation", validation), ("test", test), ("purged", purged))}


class StoryModelComparisonTests(unittest.TestCase):
    def setUp(self):
        self.baseline = index_rows([
            snapshot("old_train", 1, [(101, "Original title")]),
            snapshot("old_val", 2, [(102, "Validation title")]),
            snapshot("old_test", 3, [(103, "Old test only")]),
        ], "baseline fixture")
        self.baseline_manifest = manifest(train=["old_train"], validation=["old_val"], test=["old_test"])
        self.candidate = index_rows([
            snapshot("new_train", 90, [(190, "Candidate train")]),
            snapshot("new_val", 91, [(191, "Candidate validation")]),
        ], "candidate fixture")
        self.candidate_manifest = manifest(train=["new_train"], validation=["new_val"])

    def add_candidate(self, key, story_id, articles):
        self.candidate[key] = snapshot(key, story_id, articles)

    def filter(self, test_keys):
        return exposure_filter(self.candidate, self.baseline, self.candidate_manifest,
                               self.baseline_manifest, set(test_keys))

    def test_direct_story_article_and_normalized_title_overlap_is_excluded(self):
        self.add_candidate("story", 1, [(201, "Different headline")])
        self.add_candidate("article", 4, [(102, "Different validation headline")])
        self.add_candidate("title", 5, [(205, " ORIGINAL, title! ")])
        self.add_candidate("clean", 6, [(206, "Previously unseen report")])
        excluded, coverage = self.filter(["story", "article", "title", "clean"])

        self.assertEqual(set(excluded), {"story", "article", "title"})
        self.assertEqual(excluded["story"]["direct_match_types"], {"baseline_train": ["story_id"]})
        self.assertEqual(excluded["article"]["direct_match_types"], {"baseline_validation": ["article_id"]})
        self.assertEqual(excluded["title"]["direct_match_types"], {"baseline_train": ["normalized_title"]})
        self.assertEqual(coverage["baseline_train_or_validation"], 3)
        self.assertEqual(coverage["comparable_total"], 1)

    def test_baseline_test_only_overlap_is_retained(self):
        # All three identity signals match the old test, but none was used to fit
        # the baseline or select its threshold. It must not seed an exclusion.
        self.candidate["old_test"] = deepcopy(self.baseline["old_test"])
        excluded, coverage = self.filter(["old_test"])

        self.assertEqual(excluded, {})
        self.assertEqual(coverage["excluded_total"], 0)
        self.assertEqual(coverage["comparable_total"], 1)

    def test_uncertain_or_purged_bridge_preserves_transitive_exposure(self):
        self.add_candidate("bridge", 6, [(206, "Validation title"), (207, "Bridge report")])
        self.add_candidate("transitive", 7, [(208, "Bridge report")])
        self.candidate["bridge"]["decision"] = "uncertain"

        for bridge_is_purged in (False, True):
            with self.subTest(bridge_is_purged=bridge_is_purged):
                # Uncertain rows may be absent from every eligible split; purged
                # rows also remain in snapshots and must bridge shared evidence.
                self.candidate_manifest["purged"] = [{"key": "bridge"}] if bridge_is_purged else []
                original = deepcopy((self.candidate, self.baseline, self.candidate_manifest, self.baseline_manifest))
                excluded, coverage = self.filter(["transitive"])

                self.assertEqual(excluded["transitive"]["exposures"], ["baseline_validation"])
                self.assertEqual(excluded["transitive"]["direct_match_types"], {})
                self.assertEqual(excluded["transitive"]["transitive_exposures"], ["baseline_validation"])
                self.assertEqual(coverage["has_transitive_exposure"], 1)
                self.assertEqual(coverage["comparable_total"], 0)
                self.assertEqual((self.candidate, self.baseline, self.candidate_manifest,
                                  self.baseline_manifest), original)

    def test_candidate_own_train_and_validation_exposure_is_flagged(self):
        self.add_candidate("own_train", 8, [(209, "Candidate train")])
        self.add_candidate("own_val", 9, [(191, "New wording")])
        excluded, coverage = self.filter(["own_train", "own_val"])

        self.assertEqual(excluded["own_train"]["exposures"], ["candidate_train"])
        self.assertEqual(excluded["own_val"]["exposures"], ["candidate_validation"])
        self.assertEqual(coverage["candidate_train_or_validation"], 2)
        self.assertEqual(coverage["baseline_train_or_validation"], 0)
        self.assertEqual(coverage["excluded_total"], 2)

    def test_multiple_exposure_reasons_count_one_excluded_case(self):
        self.add_candidate("multi", 1, [(101, "Original title"), (191, "Candidate validation")])
        excluded, coverage = self.filter(["multi"])

        self.assertEqual(excluded["multi"]["exposures"], ["baseline_train", "candidate_validation"])
        self.assertEqual(coverage["excluded_total"], 1)
        self.assertEqual(coverage["baseline_train_or_validation"], 1)
        self.assertEqual(coverage["candidate_train_or_validation"], 1)

    def test_empty_normalized_titles_do_not_join_unrelated_records(self):
        self.baseline["old_train"]["articles"][0]["title"] = "!!!"
        self.add_candidate("blank", 20, [(220, " ? ")])
        excluded, coverage = self.filter(["blank"])

        self.assertEqual(excluded, {})
        self.assertEqual(coverage["comparable_total"], 1)

    def test_missing_train_or_validation_split_fails_closed(self):
        for origin in ("candidate", "baseline"):
            for split in ("train", "validation"):
                with self.subTest(origin=origin, split=split):
                    candidate_manifest = deepcopy(self.candidate_manifest)
                    baseline_manifest = deepcopy(self.baseline_manifest)
                    selected = candidate_manifest if origin == "candidate" else baseline_manifest
                    del selected[split]
                    with self.assertRaisesRegex(ValueError, f"Missing split {split}"):
                        exposure_filter(self.candidate, self.baseline, candidate_manifest,
                                        baseline_manifest, set())

    def test_missing_exposure_snapshot_fails_closed(self):
        self.baseline_manifest["train"].append({"key": "missing_snapshot"})
        with self.assertRaisesRegex(ValueError, "baseline train snapshots missing"):
            self.filter([])

    def test_duplicate_input_keys_and_split_keys_are_rejected(self):
        for rows in ([{"key": "duplicate"}, {"key": "duplicate"}], [{"key": 1}, {"key": "1"}]):
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(ValueError, "Duplicate keys"):
                    index_rows(rows, "fixture")
        with self.assertRaisesRegex(ValueError, "Duplicate keys in split test"):
            split_keys(manifest(test=["duplicate", "duplicate"]), "test")
        with self.assertRaisesRegex(ValueError, "Missing split test"):
            split_keys({}, "test")


if __name__ == "__main__":
    unittest.main()
