"""Preserve similar URLs while grouping their articles into auditable story clusters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import NewsArticle, StoryCluster, StoryClusterArticle


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def story_tokens(value: str | None) -> set[str]:
    """Normalize a Korean/Latin headline into deterministic comparison tokens."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return {token for token in TOKEN_RE.findall(normalized) if len(token) > 1}


def story_similarity(left: str | None, right: str | None) -> float:
    """Return title-token Jaccard similarity without treating it as deduplication."""
    left_tokens = story_tokens(left)
    right_tokens = story_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _fingerprint(title: str, published_at: datetime | None) -> str:
    """Create a stable new-cluster identifier while allowing stories to recur later."""
    when = published_at or datetime.now(timezone.utc)
    normalized = " ".join(sorted(story_tokens(title)))
    return hashlib.sha256(f"{normalized}|{when.date().isoformat()}".encode("utf-8")).hexdigest()


def _existing_assignment(
    db: Session,
    article_id: int,
) -> StoryClusterArticle | None:
    """Find both flushed and pending assignments in the current transaction."""
    # Session.get() does not reliably return a newly added, still-pending object.
    # Collection can encounter one article through several search queries before
    # the surrounding transaction flushes, so inspect Session.new first.
    for pending in db.new:
        if (
            isinstance(pending, StoryClusterArticle)
            and pending.article_id == article_id
        ):
            return pending
    return db.get(StoryClusterArticle, article_id)


def assign_story_cluster(
    db: Session,
    article: NewsArticle,
    settings: Settings | None = None,
) -> int:
    """Assign an article to the closest recent story and always retain the article row."""
    existing = _existing_assignment(db, article.id)
    if existing is not None:
        return existing.story_cluster_id

    settings = settings or get_settings()
    published_at = article.published_at or article.created_at or datetime.now(timezone.utc)
    cutoff = published_at - timedelta(hours=settings.story_cluster_lookback_hours)
    clusters = list(
        db.scalars(
            select(StoryCluster)
            .where(
                (StoryCluster.last_published_at.is_(None))
                | (StoryCluster.last_published_at >= cutoff)
            )
            .order_by(StoryCluster.last_published_at.desc().nullslast())
            .limit(300)
        )
    )

    best_cluster: StoryCluster | None = None
    best_score = 0.0
    for cluster in clusters:
        score = story_similarity(article.title, cluster.representative_title)
        if score > best_score:
            best_cluster, best_score = cluster, score

    if best_cluster is None or best_score < settings.story_cluster_similarity_threshold:
        fingerprint = _fingerprint(article.title, published_at)
        # An exact same-title article from the same date may have created the cluster
        # in another transaction; reuse it rather than losing either URL.
        best_cluster = db.scalar(
            select(StoryCluster).where(StoryCluster.fingerprint == fingerprint)
        )
        if best_cluster is None:
            best_cluster = StoryCluster(
                fingerprint=fingerprint,
                representative_title=article.title,
                first_published_at=published_at,
                last_published_at=published_at,
            )
            db.add(best_cluster)
            db.flush()
        best_score = 1.0
        is_representative = True
    else:
        first = best_cluster.first_published_at
        last = best_cluster.last_published_at
        best_cluster.first_published_at = min(first, published_at) if first else published_at
        best_cluster.last_published_at = max(last, published_at) if last else published_at
        is_representative = False

    db.add(
        StoryClusterArticle(
            article_id=article.id,
            story_cluster_id=best_cluster.id,
            similarity=round(best_score, 6),
            is_representative=is_representative,
        )
    )
    return best_cluster.id


def backfill_story_clusters(db: Session, limit: int = 1000) -> int:
    """Idempotently assign clusters to existing curated articles in small batches."""
    articles = list(
        db.scalars(
            select(NewsArticle)
            .outerjoin(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
            .where(StoryClusterArticle.article_id.is_(None))
            .order_by(NewsArticle.published_at, NewsArticle.id)
            .limit(limit)
        )
    )
    for article in articles:
        assign_story_cluster(db, article)
    return len(articles)
