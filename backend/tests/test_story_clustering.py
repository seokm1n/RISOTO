"""Story-cluster assignment transaction idempotency tests."""

from types import SimpleNamespace
import unittest

from app.models import StoryClusterArticle
from app.services.story_clustering import assign_story_cluster


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


if __name__ == "__main__":
    unittest.main()
