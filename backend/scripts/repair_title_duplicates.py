"""Reconcile historical exact-title duplicates without rerunning relevance models.

The v5 filter adds a deterministic rule: two article-like raw records with the
same normalized title inside 15 minutes share one curated article.  This script
applies only that new rule to existing rows, preserves raw/filter audit history,
and removes redundant company-specific analysis links.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import delete, func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    ArticleFilterResult,
    ArticleQueryHit,
    ArticleRiskAssessment,
    Company,
    CompanyArticleMatch,
    CompanyFeatureWindow,
    NewsArticle,
    RawNewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventLabel,
)
from app.services.collection_health import floor_window
from app.services.article_filtering import (
    TITLE_DUPLICATE_EXCLUDED_SOURCES,
    TITLE_DUPLICATE_WINDOW_MINUTES,
    normalize_text,
)
from app.services.risk_analysis import build_feature_window
from app.services.story_risk import process_company_risk_articles


def _within_title_window(left: RawNewsArticle, right: RawNewsArticle) -> bool:
    """Mirror the production title-window comparison without model inference."""
    for field in ("published_at", "collected_at"):
        left_at = getattr(left, field, None)
        right_at = getattr(right, field, None)
        if not isinstance(left_at, datetime) or not isinstance(right_at, datetime):
            continue
        try:
            seconds = abs((left_at - right_at).total_seconds())
        except TypeError:
            seconds = abs(
                (
                    left_at.replace(tzinfo=None) - right_at.replace(tzinfo=None)
                ).total_seconds()
            )
        return seconds <= TITLE_DUPLICATE_WINDOW_MINUTES * 60
    return False


def _copy_scores(
    target: ArticleFilterResult,
    source: ArticleFilterResult | None,
) -> None:
    """Reuse the previous model scores; the maintenance pass changes only dedup."""
    target.relevance_score = source.relevance_score if source else None
    target.advertising_score = source.advertising_score if source else None
    target.classifier_kind = source.classifier_kind if source else "rules_only"


def _eligible_for_canonical_merge(
    result: ArticleFilterResult | None,
    *,
    currently_matched: bool,
    relevance_threshold: float,
    advertising_threshold: float,
) -> bool:
    if currently_matched:
        return True
    if result is None:
        return False
    if result.decision == "accepted":
        return True
    return (
        result.relevance_score is not None
        and result.relevance_score >= relevance_threshold
        and float(result.advertising_score or 0.0) < advertising_threshold
    )


def rebuild_refiltered_windows(user_id: int) -> dict[int, dict[str, int]]:
    """Rebuild only 15-minute windows that lost a redundant curated article."""
    settings = get_settings()
    keys_by_company: dict[int, set[datetime]] = defaultdict(set)
    with SessionLocal() as db:
        company_ids = set(
            db.scalars(select(Company.id).where(Company.user_id == user_id))
        )
        if not company_ids:
            return {}
        accepted_duplicate_rows = db.execute(
            select(ArticleFilterResult, NewsArticle)
            .join(
                NewsArticle,
                NewsArticle.raw_article_id == ArticleFilterResult.raw_article_id,
            )
            .where(
                ArticleFilterResult.company_id.in_(company_ids),
                ArticleFilterResult.filter_version == settings.article_filter_version,
                ArticleFilterResult.reason == "duplicate",
                ArticleFilterResult.decision == "accepted",
            )
        )
        current_matches = set(
            db.execute(
                select(
                    CompanyArticleMatch.company_id,
                    CompanyArticleMatch.article_id,
                ).where(CompanyArticleMatch.company_id.in_(company_ids))
            )
        )
        for result, article in accepted_duplicate_rows:
            if not (result.details or {}).get("historical_title_dedup"):
                continue
            if result.curated_article_id == article.id:
                continue
            if (result.company_id, article.id) in current_matches:
                continue
            article_at = article.published_at or article.created_at
            if article_at is not None:
                keys_by_company[result.company_id].add(
                    floor_window(article_at, settings.collection_window_minutes)
                )

    results: dict[int, dict[str, int]] = {}
    for company_id, window_starts in sorted(keys_by_company.items()):
        built = 0
        scored = 0
        for window_start in sorted(window_starts):
            with SessionLocal() as db:
                existing = db.scalar(
                    select(CompanyFeatureWindow).where(
                        CompanyFeatureWindow.company_id == company_id,
                        CompanyFeatureWindow.window_start == window_start,
                    )
                )
                article_time = func.coalesce(
                    NewsArticle.published_at,
                    NewsArticle.created_at,
                )
                sources = list(
                    db.scalars(
                        select(NewsArticle.source)
                        .join(
                            CompanyArticleMatch,
                            CompanyArticleMatch.article_id == NewsArticle.id,
                        )
                        .where(
                            CompanyArticleMatch.company_id == company_id,
                            article_time >= window_start,
                            article_time
                            < window_start
                            + timedelta(minutes=settings.collection_window_minutes),
                        )
                        .distinct()
                    )
                )
                state = {
                    "data_quality": existing.data_quality if existing else "complete",
                    "successful_sources": (
                        list(existing.successful_sources or []) if existing else sources
                    ),
                    "failed_sources": (
                        list(existing.failed_sources or []) if existing else []
                    ),
                }
            window = build_feature_window(
                company_id,
                window_start,
                state["data_quality"],
                state["successful_sources"],
                state["failed_sources"],
                use_type_nli=False,
                allow_scoring=True,
                force_scoring=True,
                update_events=False,
                generate_response_drafts=False,
            )
            built += 1
            scored += int(window.risk_probability is not None)
        results[company_id] = {
            "feature_windows": built,
            "risk_scored_windows": scored,
        }
    return results


def repair_user_title_duplicates(
    user_id: int,
    *,
    apply: bool,
    rebuild_windows: bool,
) -> dict[str, object]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    totals: dict[str, int] = defaultdict(int)
    company_results: list[dict[str, int | str]] = []
    changed_company_ids: set[int] = set()
    affected_events_by_company: dict[int, set[int]] = defaultdict(set)

    with SessionLocal() as db:
        companies = list(
            db.scalars(
                select(Company)
                .where(Company.user_id == user_id)
                .order_by(Company.id)
            )
        )

    for company in companies:
        counts: dict[str, int | str] = defaultdict(int)
        counts["company_id"] = company.id
        counts["company_name"] = company.name
        with SessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"company-collection:{company.id}"},
                )
            raw_ids = list(
                db.scalars(
                    select(ArticleQueryHit.raw_article_id)
                    .where(ArticleQueryHit.company_id == company.id)
                    .distinct()
                )
            )
            matched_raw_ids = list(
                db.scalars(
                    select(NewsArticle.raw_article_id)
                    .join(
                        CompanyArticleMatch,
                        CompanyArticleMatch.article_id == NewsArticle.id,
                    )
                    .where(
                        CompanyArticleMatch.company_id == company.id,
                        NewsArticle.raw_article_id.is_not(None),
                    )
                    .distinct()
                )
            )
            raw_ids = sorted(set(raw_ids) | set(matched_raw_ids))
            if not raw_ids:
                company_results.append(dict(counts))
                continue

            raws = list(
                db.scalars(
                    select(RawNewsArticle)
                    .where(RawNewsArticle.id.in_(raw_ids))
                    .order_by(RawNewsArticle.id)
                )
            )
            raw_by_id = {raw.id: raw for raw in raws}
            by_title: dict[str, list[RawNewsArticle]] = defaultdict(list)
            parent: dict[int, int] = {}
            for raw in raws:
                source = str(raw.source or "").strip().casefold()
                if source in TITLE_DUPLICATE_EXCLUDED_SOURCES:
                    continue
                title_key = normalize_text(raw.title)
                if not title_key:
                    continue
                candidates = [
                    candidate
                    for candidate in by_title[title_key]
                    if str(candidate.source or "").strip().casefold()
                    not in TITLE_DUPLICATE_EXCLUDED_SOURCES
                    and _within_title_window(raw, candidate)
                ]
                if candidates:
                    # Historical production evaluation is raw-ID ordered and uses
                    # the first earlier matching candidate as the direct parent.
                    parent[raw.id] = candidates[0].id
                by_title[title_key].append(raw)

            counts["duplicate_raws"] = len(parent)
            if not parent:
                company_results.append(dict(counts))
                continue

            def root_id(raw_id: int) -> int:
                seen: set[int] = set()
                while raw_id in parent and raw_id not in seen:
                    seen.add(raw_id)
                    raw_id = parent[raw_id]
                return raw_id

            components: dict[int, set[int]] = defaultdict(set)
            for duplicate_id, parent_id in parent.items():
                root = root_id(duplicate_id)
                components[root].update((duplicate_id, parent_id, root))

            filter_rows = list(
                db.scalars(
                    select(ArticleFilterResult)
                    .where(
                        ArticleFilterResult.company_id == company.id,
                        ArticleFilterResult.raw_article_id.in_(raw_ids),
                    )
                    .order_by(
                        ArticleFilterResult.raw_article_id,
                        ArticleFilterResult.id.desc(),
                    )
                )
            )
            latest_by_raw: dict[int, ArticleFilterResult] = {}
            version_by_raw: dict[int, ArticleFilterResult] = {}
            for result in filter_rows:
                latest_by_raw.setdefault(result.raw_article_id, result)
                if result.filter_version == settings.article_filter_version:
                    version_by_raw[result.raw_article_id] = result

            direct_articles = {
                article.raw_article_id: article
                for article in db.scalars(
                    select(NewsArticle).where(NewsArticle.raw_article_id.in_(raw_ids))
                )
                if article.raw_article_id is not None
            }
            matched_articles = {
                article.id: article
                for article in db.scalars(
                    select(NewsArticle)
                    .join(
                        CompanyArticleMatch,
                        CompanyArticleMatch.article_id == NewsArticle.id,
                    )
                    .where(CompanyArticleMatch.company_id == company.id)
                )
            }
            matched_ids = set(matched_articles)

            for root, component in components.items():
                ordered_raw_ids = sorted(component)
                candidate_article_ids: set[int] = set()
                reference_rank: dict[int, int] = {}
                for rank, raw_id in enumerate(ordered_raw_ids):
                    direct = direct_articles.get(raw_id)
                    if direct is not None and direct.id in matched_ids:
                        candidate_article_ids.add(direct.id)
                        reference_rank.setdefault(direct.id, rank)
                    latest = latest_by_raw.get(raw_id)
                    if (
                        latest is not None
                        and latest.curated_article_id in matched_ids
                    ):
                        candidate_article_ids.add(latest.curated_article_id)
                        reference_rank.setdefault(latest.curated_article_id, rank)

                def survivor_priority(article_id: int) -> tuple[int, int, int]:
                    article = matched_articles[article_id]
                    if article.raw_article_id == root:
                        return (0, 0, article_id)
                    if article.raw_article_id in component:
                        return (
                            1,
                            ordered_raw_ids.index(article.raw_article_id),
                            article_id,
                        )
                    return (2, reference_rank.get(article_id, len(component)), article_id)

                survivor_id = (
                    min(candidate_article_ids, key=survivor_priority)
                    if candidate_article_ids
                    else None
                )
                direct_matched_ids = {
                    direct_articles[raw_id].id
                    for raw_id in ordered_raw_ids
                    if raw_id in direct_articles
                    and direct_articles[raw_id].id in matched_ids
                }
                redundant_ids = direct_matched_ids - ({survivor_id} if survivor_id else set())
                counts["duplicate_components"] += 1
                counts["redundant_matches"] += len(redundant_ids)

                accepted_sources = [
                    latest_by_raw.get(raw_id)
                    for raw_id in ordered_raw_ids
                    if latest_by_raw.get(raw_id) is not None
                    and latest_by_raw[raw_id].decision == "accepted"
                ]
                root_latest = latest_by_raw.get(root)
                root_source = (
                    root_latest
                    if root_latest is not None and root_latest.decision == "accepted"
                    else accepted_sources[0] if accepted_sources else root_latest
                )

                if apply and survivor_id is not None:
                    root_result = version_by_raw.get(root)
                    if root_result is None:
                        root_result = ArticleFilterResult(
                            raw_article_id=root,
                            company_id=company.id,
                            decision="accepted",
                            reason="accepted",
                            classifier_kind="rules_only",
                            filter_version=settings.article_filter_version,
                            details={},
                        )
                        db.add(root_result)
                        version_by_raw[root] = root_result
                        counts["filter_rows_created"] += 1
                    else:
                        counts["filter_rows_updated"] += 1
                    _copy_scores(root_result, root_source)
                    root_result.decision = "accepted"
                    root_result.reason = "accepted"
                    root_result.duplicate_of_raw_id = None
                    root_result.curated_article_id = survivor_id
                    root_result.confidence = (
                        root_source.confidence if root_source else 1.0
                    )
                    root_result.details = {
                        **(dict(root_source.details or {}) if root_source else {}),
                        "historical_title_dedup_root": True,
                        "historical_refiltered_at": now.isoformat(),
                    }
                    root_result.filtered_at = now

                for duplicate_id in ordered_raw_ids:
                    if duplicate_id not in parent:
                        continue
                    previous = latest_by_raw.get(duplicate_id)
                    direct = direct_articles.get(duplicate_id)
                    currently_matched = bool(
                        direct is not None and direct.id in matched_ids
                    )
                    merged = survivor_id is not None and _eligible_for_canonical_merge(
                        previous,
                        currently_matched=currently_matched,
                        relevance_threshold=settings.article_filter_relevance_accept_threshold,
                        advertising_threshold=settings.article_filter_advertising_review_threshold,
                    )
                    if not apply:
                        continue
                    result = version_by_raw.get(duplicate_id)
                    if result is None:
                        result = ArticleFilterResult(
                            raw_article_id=duplicate_id,
                            company_id=company.id,
                            decision="accepted" if merged else "rejected",
                            reason="duplicate",
                            classifier_kind="rules_only",
                            filter_version=settings.article_filter_version,
                            details={},
                        )
                        db.add(result)
                        version_by_raw[duplicate_id] = result
                        counts["filter_rows_created"] += 1
                    else:
                        counts["filter_rows_updated"] += 1
                    _copy_scores(result, previous)
                    result.decision = "accepted" if merged else "rejected"
                    result.reason = "duplicate"
                    result.duplicate_of_raw_id = parent[duplicate_id]
                    result.curated_article_id = survivor_id if merged else None
                    result.confidence = 1.0
                    result.details = {
                        **(dict(previous.details or {}) if previous else {}),
                        "duplicate_score": 1.0,
                        "duplicate_evidence": "same_title_within_15_minutes",
                        "historical_title_dedup": True,
                        "historical_refiltered_at": now.isoformat(),
                    }
                    result.filtered_at = now

                if apply and redundant_ids:
                    affected_event_ids = set(
                        db.scalars(
                            select(RiskEventArticle.risk_event_id)
                            .join(
                                RiskEvent,
                                RiskEvent.id == RiskEventArticle.risk_event_id,
                            )
                            .where(
                                RiskEvent.company_id == company.id,
                                RiskEventArticle.article_id.in_(redundant_ids),
                            )
                        )
                    )
                    affected_events_by_company[company.id].update(affected_event_ids)
                    db.execute(
                        delete(RiskEventArticle).where(
                            RiskEventArticle.risk_event_id.in_(affected_event_ids),
                            RiskEventArticle.article_id.in_(redundant_ids),
                        )
                    )
                    db.execute(
                        delete(ArticleRiskAssessment).where(
                            ArticleRiskAssessment.company_id == company.id,
                            ArticleRiskAssessment.article_id.in_(redundant_ids),
                        )
                    )
                    db.execute(
                        delete(CompanyArticleMatch).where(
                            CompanyArticleMatch.company_id == company.id,
                            CompanyArticleMatch.article_id.in_(redundant_ids),
                        )
                    )
                    changed_company_ids.add(company.id)

            if apply:
                db.commit()
            else:
                db.rollback()

        for key, value in counts.items():
            if isinstance(value, int) and key not in {"company_id"}:
                totals[key] += value
        company_results.append(dict(counts))

    window_results: dict[int, dict[str, int | str]] = {}
    story_results: dict[int, dict[str, int | str]] = {}
    invalidated_events = 0
    if apply:
        if rebuild_windows:
            window_results.update(rebuild_refiltered_windows(user_id))
        for company_id in sorted(changed_company_ids):
            story_results[company_id] = process_company_risk_articles(
                company_id,
                enqueue_drafts=False,
                llm_max_attempts=0,
            )

        with SessionLocal() as db:
            for company_id, event_ids in affected_events_by_company.items():
                if not event_ids:
                    continue
                reviewed_ids = set(
                    db.scalars(
                        select(RiskEventLabel.risk_event_id).where(
                            RiskEventLabel.risk_event_id.in_(event_ids),
                            RiskEventLabel.status.in_(["confirmed", "adjudicated"]),
                        )
                    )
                )
                for event in db.scalars(
                    select(RiskEvent).where(RiskEvent.id.in_(event_ids))
                ):
                    evidence_count = db.scalar(
                        select(func.count(func.distinct(RiskEventArticle.article_id))).where(
                            RiskEventArticle.risk_event_id == event.id
                        )
                    ) or 0
                    if (
                        evidence_count < settings.story_event_min_articles
                        and event.id not in reviewed_ids
                        and event.status != "dismissed"
                    ):
                        event.status = "legacy_candidate"
                        event.closed_at = now
                        event.closure_reason = "duplicate_evidence_repair"
                        invalidated_events += 1
            db.commit()

    return {
        "mode": "apply" if apply else "dry-run",
        "user_id": user_id,
        "filter_version": settings.article_filter_version,
        "title_window_minutes": TITLE_DUPLICATE_WINDOW_MINUTES,
        "excluded_sources": sorted(TITLE_DUPLICATE_EXCLUDED_SOURCES),
        "totals": dict(totals),
        "changed_company_ids": sorted(changed_company_ids),
        "invalidated_events": invalidated_events,
        "window_results": window_results,
        "story_results": story_results,
        "companies": company_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rebuild-windows", action="store_true")
    parser.add_argument("--windows-only", action="store_true")
    args = parser.parse_args()
    if args.windows_only:
        print(
            json.dumps(
                rebuild_refiltered_windows(args.user_id),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return
    result = repair_user_title_duplicates(
        args.user_id,
        apply=args.apply,
        rebuild_windows=args.rebuild_windows,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
