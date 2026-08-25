"""Non-person workflow identity used for local human-review audit rows."""

# Reviewer names are deliberately not part of the public API.  The database
# columns remain populated for compatibility with existing audit/history and
# unique constraints, using one stable non-person workflow actor.
INTERNAL_REVIEW_ACTOR = "risoto-review-workflow"

