"""Group retained article URLs into auditable, content-based story clusters.

The former implementation compared one headline per cluster with a single 0.72
Jaccard cutoff. The v2 matcher uses title and summary text, canonical event
actions/agencies/people, several article exemplars, and optional multilingual
embeddings. Publication time limits the candidate search; it is not itself
evidence that two articles describe the same event. Location is deliberately
not extracted or required because it is missing from many feeds.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
from html import unescape
import logging
import re
import unicodedata
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    ArticleRiskAssessment,
    CompanyArticleMatch,
    NewsArticle,
    StoryCluster,
    StoryClusterArticle,
)

if TYPE_CHECKING:
    from app.services.article_filtering import LocalSemanticScorer


logger = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
LEADING_NEWS_TAG_RE = re.compile(
    r"^(?:\s*[\[('‘\"]\s*(?:속보|종합|단독|영상|포토|그래픽|현장)\s*[\])'’\"]\s*)+",
    re.IGNORECASE,
)

# Headline scaffolding is excluded only from lexical tokens. Event-bearing
# terms such as "압수수색" and "사고" remain and are canonicalized below.
STOP_TOKENS = {
    "관련", "대해", "대한", "위해", "통해", "나서", "돌입", "착수",
    "밝혀", "밝혔다", "진행", "예정", "기자", "뉴스", "속보", "종합",
    "단독", "영상", "포토", "오늘", "어제", "오전", "오후",
}

# These are semantic event concepts, not a location dictionary. Synonyms from
# different publishers map to the same action, institution, or concrete fact.
CONCEPT_PATTERNS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("action:raid", ("압수수색", "강제수사", "강제 수사", "영장 집행", "영장을 집행"), 1.4),
    ("action:investigation", ("수사 착수", "조사 착수", "수사에 착수", "조사에 착수"), 1.0),
    ("action:recall", ("리콜", "회수 조치", "판매 중단", "판매중단"), 1.4),
    ("action:sanction", ("과징금", "과태료", "영업정지", "제재", "시정명령"), 1.3),
    ("action:lawsuit", ("소송", "고소", "고발", "기소", "재판"), 1.1),
    ("action:strike", ("파업", "쟁의", "작업 중단", "근무 중단"), 1.4),
    ("incident:crush", ("끼임", "끼여", "끼인", "깔림", "구조물 사이"), 1.5),
    ("incident:forklift", ("지게차", "리프트 트럭"), 1.5),
    ("incident:fire", ("화재", "불이 나", "불이나", "발화"), 1.5),
    ("incident:explosion", ("폭발", "폭발음"), 1.5),
    ("incident:fall", ("추락", "떨어져", "떨어진"), 1.4),
    ("incident:collapse", ("붕괴", "무너져", "무너진"), 1.4),
    ("incident:collision", ("충돌", "추돌"), 1.3),
    ("incident:death", ("사망", "숨져", "숨진"), 1.4),
    ("incident:serious_injury", ("중상", "중태", "의식불명", "심정지"), 1.2),
    ("incident:data_leak", ("정보 유출", "정보유출", "개인정보 유출", "개인정보유출"), 1.5),
    ("incident:hacking", ("해킹", "랜섬웨어", "악성코드", "사이버 공격"), 1.5),
    ("incident:outage", ("서비스 장애", "접속 장애", "전산 장애", "먹통"), 1.4),
    ("incident:defect", ("결함", "불량", "하자"), 1.2),
    ("incident:embezzlement", ("횡령", "배임"), 1.5),
    ("incident:fraud", ("사기", "허위 공시", "분식회계"), 1.4),
    ("object:logistics_center", ("물류센터", "물류 센터", "풀필먼트센터", "풀필먼트 센터"), 0.7),
    ("agency:police", ("경찰", "경찰청", "수사당국", "수사 당국"), 0.7),
    ("agency:prosecution", ("검찰", "검찰청"), 0.7),
    ("agency:labor", ("고용노동부", "고용부", "노동부", "노동당국", "노동 당국"), 0.7),
    ("agency:ftc", ("공정거래위원회", "공정위"), 0.7),
    ("agency:fss", ("금융감독원", "금감원"), 0.7),
    ("agency:privacy", ("개인정보보호위원회", "개보위"), 0.7),
    ("agency:court", ("대법원", "고등법원", "지방법원", "법원"), 0.6),
    ("agency:fire", ("소방당국", "소방 당국", "소방청", "소방서"), 0.6),
)
HIGH_SIGNAL_PREFIXES = ("action:", "incident:")
PERSON_RE = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})\s*(대표이사|대표|회장|사장|장관|의원|총수)(?![가-힣])"
)
DATE_FACT_RE = re.compile(r"(?:지난달\s*)?\d{1,2}일|\d{1,2}대|\d{1,3}명")


@dataclass(frozen=True, slots=True)
class StoryMatch:
    """Explainable pairwise decision used by live and rebuild clustering."""

    matched: bool
    score: float
    lexical_similarity: float
    semantic_similarity: float | None
    shared_concepts: tuple[str, ...]
    shared_terms: tuple[str, ...]
    gap_hours: float


@lru_cache(maxsize=100_000)
def normalize_story_text(value: str | None) -> str:
    """Normalize markup and headline decoration without removing locations."""
    normalized = unicodedata.normalize("NFKC", unescape(value or "")).casefold()
    normalized = TAG_RE.sub(" ", normalized)
    normalized = LEADING_NEWS_TAG_RE.sub("", normalized)
    return SPACE_RE.sub(" ", normalized).strip()


@lru_cache(maxsize=100_000)
def story_tokens(value: str | None) -> frozenset[str]:
    """Return deterministic Korean/Latin tokens with news boilerplate removed."""
    return frozenset(
        token
        for token in TOKEN_RE.findall(normalize_story_text(value))
        if len(token) > 1 and token not in STOP_TOKENS
    )


@lru_cache(maxsize=100_000)
def _compact(value: str | None) -> str:
    return "".join(TOKEN_RE.findall(normalize_story_text(value)))


def _containment(left: set[str] | frozenset[str], right: set[str] | frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _jaccard(left: set[str] | frozenset[str], right: set[str] | frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@lru_cache(maxsize=100_000)
def _char_ngrams(value: str | None, size: int = 3) -> frozenset[str]:
    compact = _compact(value)
    if not compact:
        return frozenset()
    if len(compact) <= size:
        return frozenset({compact})
    return frozenset(compact[index:index + size] for index in range(len(compact) - size + 1))


def _dice(left: set[str] | frozenset[str], right: set[str] | frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


def story_similarity(
    left: str | None,
    right: str | None,
    left_summary: str | None = None,
    right_summary: str | None = None,
) -> float:
    """Return a title-and-summary lexical score; no single cutoff is implied."""
    left_title = normalize_story_text(left)
    right_title = normalize_story_text(right)
    if not left_title or not right_title:
        return 0.0
    if _compact(left_title) == _compact(right_title):
        return 1.0

    left_tokens = story_tokens(left_title)
    right_tokens = story_tokens(right_title)
    title_containment = _containment(left_tokens, right_tokens)
    title_jaccard = _jaccard(left_tokens, right_tokens)
    title_chars = _dice(_char_ngrams(left_title), _char_ngrams(right_title))
    title_sequence = SequenceMatcher(None, _compact(left_title), _compact(right_title)).ratio()
    title_score = max(
        0.52 * title_containment + 0.48 * title_chars,
        0.55 * title_sequence + 0.45 * title_jaccard,
    )

    if not left_summary or not right_summary:
        return round(title_score, 6)
    left_body = normalize_story_text(left_summary)[:900]
    right_body = normalize_story_text(right_summary)[:900]
    body_tokens_left = story_tokens(left_body)
    body_tokens_right = story_tokens(right_body)
    body_score = max(
        0.55 * _containment(body_tokens_left, body_tokens_right)
        + 0.45 * _jaccard(body_tokens_left, body_tokens_right),
        _dice(_char_ngrams(left_body), _char_ngrams(right_body)),
    )
    return round(min(1.0, 0.78 * title_score + 0.22 * body_score), 6)


@lru_cache(maxsize=100_000)
def story_concepts(title: str | None, summary: str | None = None) -> dict[str, float]:
    """Extract canonical actions, agencies, people, and facts (never location)."""
    text = normalize_story_text(" ".join(part for part in (title, summary) if part))
    concepts = {
        key: weight
        for key, patterns, weight in CONCEPT_PATTERNS
        if any(pattern in text for pattern in patterns)
    }
    for name, role in PERSON_RE.findall(text):
        concepts[f"person:{name}:{role}"] = 1.0
    for fact in DATE_FACT_RE.findall(text):
        concepts[f"fact:{SPACE_RE.sub('', fact)}"] = 0.35
    return concepts


def _shared_terms(
    left_title: str | None,
    right_title: str | None,
    left_summary: str | None,
    right_summary: str | None,
) -> set[str]:
    left = story_tokens(" ".join(part for part in (left_title, left_summary) if part))
    right = story_tokens(" ".join(part for part in (right_title, right_summary) if part))
    return {token for token in left & right if len(token) >= 3 and token not in STOP_TOKENS}


def _shared_title_terms(left_title: str | None, right_title: str | None) -> set[str]:
    return {
        token
        for token in story_tokens(left_title) & story_tokens(right_title)
        if len(token) >= 3 and token not in STOP_TOKENS
    }


def match_story_articles(
    left_title: str | None,
    right_title: str | None,
    *,
    left_summary: str | None = None,
    right_summary: str | None = None,
    semantic_similarity: float | None = None,
    gap_hours: float = 0.0,
    recent_hours: int = 168,
    followup_hours: int = 720,
) -> StoryMatch:
    """Decide whether two articles describe one event using explainable signals."""
    lexical = story_similarity(left_title, right_title, left_summary, right_summary)
    left_concepts = story_concepts(left_title, left_summary)
    right_concepts = story_concepts(right_title, right_summary)
    shared_concepts = set(left_concepts) & set(right_concepts)
    concept_weight = sum(min(left_concepts[key], right_concepts[key]) for key in shared_concepts)
    high_signal_count = sum(key.startswith(HIGH_SIGNAL_PREFIXES) for key in shared_concepts)
    terms = _shared_terms(left_title, right_title, left_summary, right_summary)
    title_terms = _shared_title_terms(left_title, right_title)
    semantic = None if semantic_similarity is None else max(-1.0, min(1.0, semantic_similarity))
    exact_title = bool(_compact(left_title)) and _compact(left_title) == _compact(right_title)
    within_recent = gap_hours <= recent_hours
    within_followup = gap_hours <= followup_hours

    if not within_followup:
        matched = False
    elif exact_title:
        matched = True
    elif within_recent:
        matched = (
            lexical >= 0.66
            or (lexical >= 0.43 and high_signal_count >= 1 and concept_weight >= 1.8)
            or (lexical >= 0.30 and high_signal_count >= 2 and concept_weight >= 3.0)
            or (
                semantic is not None
                and semantic >= 0.80
                and lexical >= 0.34
                and (high_signal_count >= 1 or len(title_terms) >= 2)
            )
            or (
                semantic is not None
                and semantic >= 0.58
                and lexical >= 0.28
                and high_signal_count >= 2
                and concept_weight >= 3.0
            )
            or (
                semantic is not None
                and semantic >= 0.58
                and lexical >= 0.15
                and high_signal_count >= 3
                and concept_weight >= 4.0
            )
        )
    else:
        matched = (
            lexical >= 0.78
            or (
                semantic is not None
                and semantic >= 0.82
                and lexical >= 0.30
                and high_signal_count >= 2
                and concept_weight >= 3.0
            )
        )

    semantic_component = max(0.0, semantic or 0.0)
    concept_component = min(1.0, concept_weight / 5.0)
    score = min(1.0, 0.60 * lexical + 0.30 * semantic_component + 0.10 * concept_component)
    if exact_title:
        score = 1.0
    return StoryMatch(
        matched=matched,
        score=round(score, 6),
        lexical_similarity=lexical,
        semantic_similarity=None if semantic is None else round(semantic, 6),
        shared_concepts=tuple(sorted(shared_concepts)),
        shared_terms=tuple(sorted(terms)),
        gap_hours=round(gap_hours, 3),
    )


def story_article_text(article: NewsArticle) -> str:
    """Build the semantic input with headline emphasis and bounded summary text."""
    title = normalize_story_text(article.title)
    summary = normalize_story_text(article.summary)[:1200]
    return f"{title}. {title}. {summary}".strip()


def _article_time(article: NewsArticle) -> datetime:
    value = article.published_at or article.created_at or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _fingerprint(title: str, published_at: datetime | None) -> str:
    """Create a stable v2 seed identifier without colliding with legacy hashes."""
    when = published_at or datetime.now(timezone.utc)
    normalized = " ".join(sorted(story_tokens(title)))
    return hashlib.sha256(f"story-cluster-v2|{normalized}|{when.date().isoformat()}".encode("utf-8")).hexdigest()


def _existing_assignment(db: Session, article_id: int) -> StoryClusterArticle | None:
    """Find both flushed and pending assignments in the current transaction."""
    for pending in db.new:
        if isinstance(pending, StoryClusterArticle) and pending.article_id == article_id:
            return pending
    return db.get(StoryClusterArticle, article_id)


def _candidate_rows(
    db: Session,
    article: NewsArticle,
    settings: Settings,
    company_id: int | None,
) -> list[tuple[NewsArticle, StoryClusterArticle]]:
    published_at = _article_time(article)
    horizon = timedelta(hours=settings.story_cluster_followup_hours)
    article_time = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    query = (
        select(NewsArticle, StoryClusterArticle)
        .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
        .where(
            NewsArticle.id != article.id,
            article_time >= published_at - horizon,
            article_time <= published_at + horizon,
        )
    )
    if company_id is not None:
        query = query.join(
            CompanyArticleMatch,
            CompanyArticleMatch.article_id == NewsArticle.id,
        ).where(CompanyArticleMatch.company_id == company_id)
    return db.execute(
        query.order_by(article_time.desc(), NewsArticle.id.desc())
        .limit(settings.story_cluster_candidate_limit)
    ).all()


def assign_story_cluster(
    db: Session,
    article: NewsArticle,
    settings: Settings | None = None,
    *,
    company_id: int | None = None,
    semantic_scorer: LocalSemanticScorer | None = None,
) -> int:
    """Assign one article using multiple recent exemplars and optional semantics."""
    existing = _existing_assignment(db, article.id)
    if existing is not None:
        return existing.story_cluster_id

    settings = settings or get_settings()
    published_at = _article_time(article)
    rows = _candidate_rows(db, article, settings, company_id)
    preliminary: list[tuple[float, NewsArticle, StoryClusterArticle]] = []
    article_concepts = set(story_concepts(article.title, article.summary))
    for candidate, link in rows:
        lexical = story_similarity(article.title, candidate.title, article.summary, candidate.summary)
        shared = article_concepts & set(story_concepts(candidate.title, candidate.summary))
        preliminary.append((lexical + min(0.15, 0.025 * len(shared)), candidate, link))
    preliminary.sort(key=lambda item: item[0], reverse=True)
    compared = preliminary[: settings.story_cluster_semantic_candidate_limit]

    semantics: list[float] | None = None
    if semantic_scorer is not None and compared:
        semantics = semantic_scorer.similarities(
            story_article_text(article),
            [story_article_text(candidate) for _, candidate, _ in compared],
        )
    if semantics is None:
        semantics = [None] * len(compared)

    best_link: StoryClusterArticle | None = None
    best_match: StoryMatch | None = None
    for (_, candidate, link), semantic in zip(compared, semantics):
        gap = abs((published_at - _article_time(candidate)).total_seconds()) / 3600.0
        result = match_story_articles(
            article.title,
            candidate.title,
            left_summary=article.summary,
            right_summary=candidate.summary,
            semantic_similarity=semantic,
            gap_hours=gap,
            recent_hours=settings.story_cluster_recent_hours,
            followup_hours=settings.story_cluster_followup_hours,
        )
        if result.matched and (best_match is None or result.score > best_match.score):
            best_link, best_match = link, result

    if best_link is None:
        fingerprint = _fingerprint(article.title, published_at)
        cluster = db.scalar(select(StoryCluster).where(StoryCluster.fingerprint == fingerprint))
        if cluster is None:
            cluster = StoryCluster(
                fingerprint=fingerprint,
                representative_title=article.title,
                first_published_at=published_at,
                last_published_at=published_at,
            )
            db.add(cluster)
            db.flush()
        similarity = 1.0
        is_representative = True
    else:
        cluster = db.get(StoryCluster, best_link.story_cluster_id)
        if cluster is None:
            raise RuntimeError(f"story cluster {best_link.story_cluster_id} disappeared")
        cluster.first_published_at = min(cluster.first_published_at, published_at) if cluster.first_published_at else published_at
        cluster.last_published_at = max(cluster.last_published_at, published_at) if cluster.last_published_at else published_at
        similarity = best_match.score
        is_representative = False

    db.add(
        StoryClusterArticle(
            article_id=article.id,
            story_cluster_id=cluster.id,
            similarity=similarity,
            is_representative=is_representative,
        )
    )
    return cluster.id


def _chunks(values: list, size: int = 1000):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def recluster_story_articles(
    db: Session,
    *,
    company_id: int | None = None,
    cutoff: datetime | None = None,
    settings: Settings | None = None,
    semantic_scorer: LocalSemanticScorer | None = None,
) -> dict[str, int | bool]:
    """Recompute v2 clusters in memory and update links without deleting history."""
    settings = settings or get_settings()
    context_cutoff = cutoff - timedelta(hours=settings.story_cluster_followup_hours) if cutoff else None
    article_time_sql = func.coalesce(NewsArticle.published_at, NewsArticle.created_at)
    matched_article_ids = select(CompanyArticleMatch.article_id)
    if company_id is not None:
        matched_article_ids = matched_article_ids.where(
            CompanyArticleMatch.company_id == company_id
        )
    article_query = select(NewsArticle).where(
        NewsArticle.id.in_(matched_article_ids)
    )
    if context_cutoff is not None:
        article_query = article_query.where(article_time_sql >= context_cutoff)
    articles = list(db.scalars(article_query.order_by(article_time_sql, NewsArticle.id)))
    if not articles:
        return {"articles": 0, "clusters": 0, "links_changed": 0, "semantic_used": False}

    article_ids = [article.id for article in articles]
    company_map: dict[int, set[int]] = defaultdict(set)
    for chunk in _chunks(article_ids):
        for article_id, matched_company_id in db.execute(
            select(CompanyArticleMatch.article_id, CompanyArticleMatch.company_id).where(
                CompanyArticleMatch.article_id.in_(chunk)
            )
        ):
            company_map[article_id].add(matched_company_id)

    if semantic_scorer is None and settings.article_filter_ai_enabled:
        from app.services.article_filtering import FilterConfig, get_semantic_scorer

        semantic_scorer = get_semantic_scorer(
            FilterConfig(
                ai_enabled=True,
                semantic_model_name=settings.article_filter_semantic_model,
                allow_model_download=settings.article_filter_allow_model_download,
            )
        )
    embeddings = (
        semantic_scorer.embeddings(
            [story_article_text(article) for article in articles],
            batch_size=settings.story_cluster_embedding_batch_size,
        )
        if semantic_scorer is not None
        else None
    )

    times = [_article_time(article) for article in articles]
    concepts = [story_concepts(article.title, article.summary) for article in articles]
    title_token_sets = [story_tokens(article.title) for article in articles]
    histories: dict[int, list[int]] = defaultdict(list)
    anchor_histories: dict[tuple[int, str], list[int]] = defaultdict(list)
    unowned_history: list[int] = []
    parents: list[int] = []
    similarities: list[float] = []
    candidate_limit = settings.story_cluster_candidate_limit
    followup_delta = timedelta(hours=settings.story_cluster_followup_hours)

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        # The earliest article remains the stable seed/fingerprint.
        if left_root < right_root:
            parents[right_root] = left_root
        else:
            parents[left_root] = right_root

    for index, article in enumerate(articles):
        parents.append(index)
        owners = company_map.get(article.id) or set()
        candidates: set[int] = set()
        owner_histories = [histories[owner] for owner in owners] if owners else [unowned_history]
        for history in owner_histories:
            for candidate_index in reversed(history[-candidate_limit:]):
                if times[index] - times[candidate_index] > followup_delta:
                    break
                candidates.add(candidate_index)
        for owner in owners:
            for concept in concepts[index]:
                for candidate_index in reversed(anchor_histories[(owner, concept)][-120:]):
                    if times[index] - times[candidate_index] > followup_delta:
                        break
                    candidates.add(candidate_index)

        best_index: int | None = None
        best_match: StoryMatch | None = None
        matched_candidates: list[tuple[int, StoryMatch]] = []
        candidate_indexes = sorted(candidates)
        evaluation_candidates: set[int] = set(candidate_indexes[-20:])
        semantic_values: dict[int, float] = {}
        semantic_limit = settings.story_cluster_semantic_candidate_limit
        if embeddings is not None and candidate_indexes:
            import numpy as np

            candidate_array = np.asarray(candidate_indexes, dtype=np.int64)
            values = embeddings[candidate_array] @ embeddings[index]
            take = min(semantic_limit, len(candidate_indexes))
            top_positions = np.argpartition(values, -take)[-take:]
            for position in top_positions:
                candidate_index = candidate_indexes[int(position)]
                evaluation_candidates.add(candidate_index)
                semantic_values[candidate_index] = float(values[int(position)])
        elif candidate_indexes:
            evaluation_candidates.update(candidate_indexes[-semantic_limit:])

        auxiliary_limit = max(20, semantic_limit // 2)
        evaluation_candidates.update(
            sorted(
                candidate_indexes,
                key=lambda candidate_index: (
                    sum(
                        min(concepts[index][key], concepts[candidate_index][key])
                        for key in concepts[index].keys() & concepts[candidate_index].keys()
                    ),
                    len(title_token_sets[index] & title_token_sets[candidate_index]),
                ),
                reverse=True,
            )[:auxiliary_limit]
        )
        evaluation_candidates.update(
            sorted(
                candidate_indexes,
                key=lambda candidate_index: len(
                    title_token_sets[index] & title_token_sets[candidate_index]
                ),
                reverse=True,
            )[:auxiliary_limit]
        )

        for candidate_index in evaluation_candidates:
            gap = max(0.0, (times[index] - times[candidate_index]).total_seconds() / 3600.0)
            semantic = (
                semantic_values.get(candidate_index)
                if embeddings is not None
                else None
            )
            if embeddings is not None and semantic is None:
                semantic = float(embeddings[index] @ embeddings[candidate_index])
            candidate = articles[candidate_index]
            result = match_story_articles(
                article.title,
                candidate.title,
                left_summary=article.summary,
                right_summary=candidate.summary,
                semantic_similarity=semantic,
                gap_hours=gap,
                recent_hours=settings.story_cluster_recent_hours,
                followup_hours=settings.story_cluster_followup_hours,
            )
            if result.matched:
                matched_candidates.append((candidate_index, result))
                if best_match is None or result.score > best_match.score:
                    best_index, best_match = candidate_index, result

        if best_index is None:
            similarities.append(1.0)
        else:
            union(index, best_index)
            for candidate_index, result in matched_candidates:
                shared_high_signals = sum(
                    key.startswith(HIGH_SIGNAL_PREFIXES)
                    for key in result.shared_concepts
                )
                if result.score >= 0.48 or (
                    shared_high_signals >= 2
                    and result.lexical_similarity >= 0.30
                ):
                    union(index, candidate_index)
            similarities.append(best_match.score)

        if owners:
            for owner in owners:
                histories[owner].append(index)
                for concept in concepts[index]:
                    anchor_histories[(owner, concept)].append(index)
        else:
            unowned_history.append(index)

    labels = [find(index) for index in range(len(articles))]
    members: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        members[label].append(index)
    fingerprints = {label: _fingerprint(articles[label].title, times[label]) for label in members}
    existing_clusters: dict[str, StoryCluster] = {}
    fingerprint_values = list(fingerprints.values())
    for chunk in _chunks(fingerprint_values):
        for cluster in db.scalars(select(StoryCluster).where(StoryCluster.fingerprint.in_(chunk))):
            existing_clusters[cluster.fingerprint] = cluster

    clusters_by_label: dict[int, StoryCluster] = {}
    representatives: dict[int, int] = {}
    for label, indexes in members.items():
        fingerprint = fingerprints[label]
        representative_index = max(
            indexes,
            key=lambda item: (
                len(concepts[item]),
                bool(articles[item].summary),
                min(len(normalize_story_text(articles[item].title)), 100),
                -item,
            ),
        )
        representatives[label] = representative_index
        cluster = existing_clusters.get(fingerprint)
        if cluster is None:
            cluster = StoryCluster(
                fingerprint=fingerprint,
                representative_title=articles[representative_index].title,
            )
            db.add(cluster)
            existing_clusters[fingerprint] = cluster
        cluster.representative_title = articles[representative_index].title
        cluster.first_published_at = min(times[item] for item in indexes)
        cluster.last_published_at = max(times[item] for item in indexes)
        clusters_by_label[label] = cluster
    db.flush()

    existing_links: dict[int, StoryClusterArticle] = {}
    for chunk in _chunks(article_ids):
        for link in db.scalars(
            select(StoryClusterArticle).where(StoryClusterArticle.article_id.in_(chunk))
        ):
            existing_links[link.article_id] = link

    changed = 0
    assignment_ids: dict[int, int] = {}
    now = datetime.now(timezone.utc)
    for index, article in enumerate(articles):
        if cutoff is not None and times[index] < cutoff:
            continue
        cluster = clusters_by_label[labels[index]]
        assignment_ids[article.id] = cluster.id
        link = existing_links.get(article.id)
        if link is None:
            link = StoryClusterArticle(article_id=article.id, story_cluster_id=cluster.id)
            db.add(link)
            changed += 1
        elif link.story_cluster_id != cluster.id:
            link.story_cluster_id = cluster.id
            changed += 1
        link.similarity = similarities[index]
        link.is_representative = index == representatives[labels[index]]
        link.assigned_at = now

    scoped_ids = list(assignment_ids)
    assessment_updates = 0
    for chunk in _chunks(scoped_ids):
        for assessment in db.scalars(
            select(ArticleRiskAssessment).where(ArticleRiskAssessment.article_id.in_(chunk))
        ):
            next_cluster_id = assignment_ids[assessment.article_id]
            if assessment.story_cluster_id != next_cluster_id:
                assessment.story_cluster_id = next_cluster_id
                assessment_updates += 1
    db.flush()
    scoped_labels = {
        labels[index]
        for index, article in enumerate(articles)
        if article.id in assignment_ids
    }
    logger.info(
        "story v2 recluster complete articles=%s clusters=%s links_changed=%s assessments_changed=%s semantic=%s",
        len(assignment_ids), len(scoped_labels), changed, assessment_updates, embeddings is not None,
    )
    return {
        "articles": len(assignment_ids),
        "clusters": len(scoped_labels),
        "links_changed": changed,
        "assessments_changed": assessment_updates,
        "semantic_used": embeddings is not None,
    }


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
        company_id = db.scalar(
            select(CompanyArticleMatch.company_id)
            .where(CompanyArticleMatch.article_id == article.id)
            .order_by(CompanyArticleMatch.company_id)
            .limit(1)
        )
        assign_story_cluster(db, article, company_id=company_id)
    return len(articles)


__all__ = [
    "StoryMatch",
    "assign_story_cluster",
    "backfill_story_clusters",
    "match_story_articles",
    "normalize_story_text",
    "recluster_story_articles",
    "story_article_text",
    "story_concepts",
    "story_similarity",
    "story_tokens",
]
