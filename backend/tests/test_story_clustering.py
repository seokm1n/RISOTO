"""Story-cluster assignment transaction idempotency tests."""

from types import SimpleNamespace
import unittest

from app.models import StoryClusterArticle
from app.services.story_clustering import assign_story_cluster, match_story_articles


class PendingAssignmentSession:
    """Minimal session double whose pending assignment must avoid a DB lookup."""

    def __init__(self, assignment: StoryClusterArticle):
        self.new = {assignment}
        self.get_called = False

    def get(self, *_args):
        self.get_called = True
        raise AssertionError("pending assignment should be reused before Session.get")


class StoryClusteringTests(unittest.TestCase):
    def test_repeated_assignment_reuses_pending_row_before_flush(self):
        assignment = StoryClusterArticle(
            article_id=14290,
            story_cluster_id=12134,
            similarity=1.0,
            is_representative=True,
        )
        db = PendingAssignmentSession(assignment)

        cluster_id = assign_story_cluster(db, SimpleNamespace(id=14290))

        self.assertEqual(cluster_id, 12134)
        self.assertFalse(db.get_called)

    def test_paraphrased_accident_with_missing_location_is_one_story(self):
        result = match_story_articles(
            "경찰 및 고용노동부, 쿠팡 물류센터 끼임사고 관련 압수수색나서",
            "쿠팡 지게차 작업자 의식불명…경찰·노동부 강제수사",
            left_summary="지게차 작업자가 구조물에 끼여 중태에 빠진 사고를 수사 중이다.",
            right_summary="물류센터 노동자 끼임 사고와 관련해 압수수색 영장이 집행됐다.",
            semantic_similarity=0.61,
            gap_hours=5,
        )

        self.assertTrue(result.matched)
        self.assertIn("incident:crush", result.shared_concepts)
        self.assertIn("action:raid", result.shared_concepts)

    def test_different_regulatory_investigation_does_not_merge_with_accident(self):
        result = match_story_articles(
            "경찰·노동부, 쿠팡 물류센터 끼임사고 압수수색",
            "공정위, 쿠팡 본사 현장조사…공정거래법 위반 의혹",
            left_summary="지게차 작업자 중태 사고에 경찰이 강제수사에 착수했다.",
            right_summary="시장지배력 남용 여부를 확인하기 위한 현장조사가 재개됐다.",
            semantic_similarity=0.76,
            gap_hours=2,
        )

        self.assertFalse(result.matched)

    def test_time_does_not_merge_recurring_story_forever(self):
        result = match_story_articles(
            "서비스 접속 장애 발생",
            "서비스 접속 장애 발생",
            semantic_similarity=1.0,
            gap_hours=24 * 31,
        )

        self.assertFalse(result.matched)


if __name__ == "__main__":
    unittest.main()
