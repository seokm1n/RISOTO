"""Offline story-risk v1 contract, shared by training and inference.

No operational article risk predictions or generated labels are input features.
This candidate is deliberately separate from the production 15-minute models.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
import re

import numpy as np

SCHEMA_VERSION = "story-snapshot-v1"
MAX_ARTICLES = 8
SUMMARY_LIMIT = 1000
PATTERNS = {
    "product_quality": ("리콜", "불량", "결함", "하자", "오작동", "recall", "defect"),
    "safety_accident": ("사고", "화재", "부상", "사망", "폭발", "중독"),
    "security_privacy": ("해킹", "유출", "개인정보", "랜섬웨어", "침해"),
    "legal_regulatory": ("소송", "공정위", "검찰", "기소", "과징금", "법원", "위반"),
    "labor_hr": ("파업", "노조", "해고", "괴롭힘", "산재", "임금"),
    "financial_governance": ("횡령", "배임", "부도", "적자", "분식", "지배구조"),
    "supply_operations": ("공급망", "배송", "물류", "중단", "품절", "장애", "생산 차질"),
    "reputation_consumer": ("불매", "논란", "항의", "민원", "비판", "갑질", "소비자 피해"),
}
NUMERIC_FEATURES = [
    "log_article_count", "log_publisher_count", "publisher_ratio", "duplicate_title_ratio",
    "span_hours", "recent_6h_ratio", "negative_mean", "negative_max", "negative_std",
    "sentiment_missing_ratio", "official_source_ratio", "company_mention_ratio",
    "mean_text_length",
] + [f"keyword_{key}_{stat}" for key in PATTERNS for stat in ("mean", "max")]


def timestamp(value: str) -> datetime:
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("Snapshot timestamps must include a timezone")
    return value


def snapshot_articles(story: dict) -> list[dict]:
    cutoff = timestamp(story["as_of"])
    articles = sorted(
        (a for a in story["articles"] if timestamp(a["available_at"]) <= cutoff),
        key=lambda a: (timestamp(a["available_at"]), a["id"]),
    )[:MAX_ARTICLES]
    if not articles or len({a["id"] for a in articles}) != len(articles):
        raise ValueError("A story needs unique, available evidence articles")
    return articles


def story_text(story: dict) -> str:
    # Company names are masked to reduce company-identity memorization.
    body = "\n".join(a["title"] + " " + (a.get("summary") or "")[:SUMMARY_LIMIT]
                     for a in snapshot_articles(story))
    for name in sorted([story["company_name"], *story.get("aliases", [])], key=len, reverse=True):
        if len(name) >= 2:
            body = re.sub(re.escape(name), " 대상기업 ", body, flags=re.IGNORECASE)
    return body


def numeric_features(story: dict) -> list[float]:
    articles = snapshot_articles(story)
    n = len(articles)
    cutoff = timestamp(story["as_of"])
    times = [timestamp(a["available_at"]) for a in articles]
    bodies = [(a["title"] + " " + (a.get("summary") or "")[:SUMMARY_LIMIT]).casefold()
              for a in articles]
    domains = [(urlsplit(a.get("url") or "").hostname or "").removeprefix("www.") for a in articles]
    negative = [float(a["negative_probability"]) for a in articles if a.get("negative_probability") is not None]
    names = [s.casefold() for s in [story["company_name"], *story.get("aliases", [])] if s]
    values = [
        np.log1p(n), np.log1p(len(set(domains) - {""})), len(set(domains) - {""}) / n,
        1 - len({re.sub(r"\W", "", a["title"]).casefold() for a in articles}) / n,
        (max(times) - min(times)).total_seconds() / 3600,
        sum((cutoff - t).total_seconds() <= 21600 for t in times) / n,
        np.mean(negative) if negative else 0, max(negative, default=0), np.std(negative) if negative else 0,
        1 - len(negative) / n,
        sum(d.endswith((".go.kr", ".gov", ".gov.kr")) for d in domains) / n,
        sum(any(name in body for name in names) for body in bodies) / n,
        np.mean([len(body) for body in bodies]),
    ]
    for patterns in PATTERNS.values():
        hits = [sum(p in body for p in patterns) for body in bodies]
        values.extend([np.mean(hits), max(hits)])
    result = np.asarray(values, dtype=float)
    if not np.isfinite(result).all():
        raise ValueError("Non-finite story feature")
    return result.tolist()


def transform_stories(bundle: dict, stories: list[dict], *, include_if: bool = True) -> np.ndarray:
    if bundle["schema_version"] != SCHEMA_VERSION or bundle["numeric_features"] != NUMERIC_FEATURES:
        raise ValueError("Incompatible story model feature contract")
    numeric = np.asarray([numeric_features(s) for s in stories])
    semantic = bundle["svd"].transform(bundle["tfidf"].transform([story_text(s) for s in stories]))
    parts = [numeric, semantic]
    if include_if:
        scores = -bundle["isolation_forest"].decision_function(bundle["scaler"].transform(numeric))
        reference = bundle["if_reference_scores"]
        percentiles = np.searchsorted(reference, scores, side="right") / len(reference)
        parts.extend([scores[:, None], percentiles[:, None]])
    return np.column_stack(parts)


def predict_stories(artifact: str | Path, stories: list[dict]) -> list[dict]:
    """Load a trusted local artifact; never load untrusted joblib files."""
    import joblib
    if not stories:
        return []
    bundle = joblib.load(artifact)
    matrix = transform_stories(bundle, stories)
    probabilities = bundle["lightgbm"].predict_proba(matrix)[:, 1]
    return [dict(story_id=s["story_id"], company_id=s["company_id"],
                 risk_probability=float(p), is_risk=bool(p >= bundle["threshold"]),
                 anomaly_score=float(x[-2]), anomaly_percentile=float(x[-1]),
                 threshold=bundle["threshold"], model_version=bundle["version"],
                 model_state="candidate", label_source="ai_generated_unreviewed")
            for s, p, x in zip(stories, probabilities, matrix)]
