"""기사별 위험 판정과 스토리 중심 운영 사건 집계.

15분 특징 창은 확산 신호와 대시보드 지표로 유지하되 사건의 정체성은 기업, 스토리
군집, 주요 위험 유형으로 결정한다. 운영 추론 결과는 사람 정답 라벨과 분리해 저장한다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    ArticleFilterResult,
    ArticleRiskAssessment,
    Company,
    CompanyArticleMatch,
    CompanyKeyword,
    CompanyFeatureWindow,
    NewsArticle,
    RiskEvent,
    RiskEventArticle,
    RiskEventType,
    StoryCluster,
    StoryClusterArticle,
)
from app.risk_taxonomy import RISK_TYPES
from app.services.risk_analysis import (
    RISK_TYPE_PATTERNS,
    resolve_article_risk_type_scores_batch,
    resolve_risk_type_scores,
)


logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="story-risk")
ENGINE_VERSION = "story-risk-hybrid-v1"
EVENT_ENGINE_VERSION = "story-event-hybrid-v2"
GOVERNMENT_SUFFIXES = (".go.kr", ".gov", ".gov.kr")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _article_time(article: NewsArticle) -> datetime:
    value = article.published_at or article.created_at or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def source_domain(url: str | None) -> str:
    try:
        host = (urlsplit(url or "").hostname or "unknown").casefold()
    except ValueError:
        return "unknown"
    return host[4:] if host.startswith("www.") else host


def source_credibility(domain: str) -> float:
    """감사 가능한 보수적 출처 점수. 제공자명이 아닌 원문 URL 도메인을 사용한다."""
    if domain == "unknown":
        return 0.35
    if domain.endswith(GOVERNMENT_SUFFIXES) or domain in {"go.kr", "gov.kr"}:
        return 0.95
    if domain in {"youtube.com", "youtu.be"}:
        return 0.35
    if "blog." in domain or domain.endswith("blogspot.com"):
        return 0.40
    return 0.65


def meets_event_threshold(
    probabilities: list[float],
    source_domains: list[str],
    settings: Settings,
) -> bool:
    """민감 모드: 고위험 한 건 또는 서로 다른 출처의 후보 두 건이면 사건화한다."""
    if any(value >= settings.article_risk_high_threshold for value in probabilities):
        return True
    domains = {value for value in source_domains if value != "unknown"}
    return len(domains) >= settings.story_event_min_distinct_sources


def _latest_filter(db: Session, company_id: int, article_id: int) -> ArticleFilterResult | None:
    return db.scalar(
        select(ArticleFilterResult)
        .where(
            ArticleFilterResult.company_id == company_id,
            ArticleFilterResult.curated_article_id == article_id,
            ArticleFilterResult.decision == "accepted",
        )
        .order_by(ArticleFilterResult.id.desc())
        .limit(1)
    )


def _local_assessment(
    article: NewsArticle,
    relevance_score: float,
    settings: Settings,
) -> dict:
    body = " ".join(part for part in (article.title, article.summary or "") if part).strip()
    negative = float(article.negative_probability or 0.0)
    keyword_hit = any(
        pattern.casefold() in body.casefold()
        for patterns in RISK_TYPE_PATTERNS.values()
        for pattern in patterns
    )
    scores = resolve_risk_type_scores(
        [body],
        risk_keyword_count=1 if keyword_hit else 0,
        negative_probability=negative,
        settings=settings,
    )
    return _local_assessment_from_scores(article, relevance_score, scores, keyword_hit, settings)


def _local_assessment_from_scores(
    article: NewsArticle,
    relevance_score: float,
    scores: dict[str, float],
    keyword_hit: bool,
    settings: Settings,
) -> dict:
    negative = float(article.negative_probability or 0.0)
    primary_type, type_probability = max(scores.items(), key=lambda item: item[1])
    probability = _clamp(
        0.45 * float(type_probability)
        + 0.35 * negative
        + 0.20 * relevance_score
    )
    if type_probability < 0.20 or probability < settings.article_risk_uncertain_low:
        decision = "non_risk"
    elif probability >= settings.article_risk_candidate_threshold and type_probability >= 0.35:
        decision = "risk"
    else:
        decision = "uncertain"
    return {
        "decision": decision,
        "risk_probability": round(probability, 6),
        "type_scores": {key: round(_clamp(value), 6) for key, value in scores.items()},
        "primary_type": primary_type if decision != "non_risk" else None,
        "classifier_kind": "rules_nli",
        "model_version": ENGINE_VERSION,
        "reason": (
            f"relevance={relevance_score:.3f}, negative={negative:.3f}, "
            f"type={primary_type}:{type_probability:.3f}"
        ),
    }


def _local_assessment_batch(
    items: list[tuple[NewsArticle, float]],
    settings: Settings,
) -> dict[int, dict]:
    """Batch the expensive local type model while preserving per-article decisions."""
    bodies = [
        " ".join(part for part in (article.title, article.summary or "") if part).strip()
        for article, _relevance in items
    ]
    keyword_hits = [
        any(
            pattern.casefold() in body.casefold()
            for patterns in RISK_TYPE_PATTERNS.values()
            for pattern in patterns
        )
        for body in bodies
    ]
    negatives = [float(article.negative_probability or 0.0) for article, _relevance in items]
    score_rows = resolve_article_risk_type_scores_batch(
        bodies,
        risk_keyword_hits=keyword_hits,
        negative_probabilities=negatives,
        settings=settings,
    )
    return {
        article.id: _local_assessment_from_scores(
            article,
            relevance,
            scores,
            keyword_hit,
            settings,
        )
        for (article, relevance), scores, keyword_hit in zip(items, score_rows, keyword_hits)
    }


def _risk_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_risk": {"type": "boolean"},
            "risk_probability": {"type": "number", "minimum": 0, "maximum": 1},
            "primary_type": {"type": ["string", "null"], "enum": [*RISK_TYPES, None]},
            "type_scores": {
                "type": "object",
                "additionalProperties": False,
                "properties": {item: {"type": "number", "minimum": 0, "maximum": 1} for item in RISK_TYPES},
                "required": list(RISK_TYPES),
            },
            "reason": {"type": "string"},
        },
        "required": ["is_risk", "risk_probability", "primary_type", "type_scores", "reason"],
    }


def _llm_assessment(
    db: Session,
    company: Company,
    article: NewsArticle,
    settings: Settings,
) -> dict | None:
    if not settings.llm_labeling_enabled:
        return None
    aliases = list(
        db.scalars(
            select(CompanyKeyword.value).where(
                CompanyKeyword.company_id == company.id,
                CompanyKeyword.keyword_type == "alias",
            )
        )
    )
    prompt = {
        "task": "기사 한 건이 해당 기업의 실제 운영 위험 사건에 관한 것인지 판정하세요.",
        "company": {"name": company.name, "aliases": aliases},
        "article": {"title": article.title, "summary": article.summary or "", "url": article.url},
        "risk_types": list(RISK_TYPES),
        "rules": [
            "단순 시장 전망, 주가 등락, 동명이인, 스포츠, 홍보성 언급은 위험이 아니다.",
            "피해, 장애, 제재, 사고, 보안 침해처럼 기업이 대응해야 할 구체적 사건만 위험이다.",
            "근거가 부족하면 낮은 확률을 주고 primary_type은 null로 둔다.",
        ],
    }
    try:
        if settings.llm_labeling_provider == "ollama":
            import httpx

            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                    json={
                        "model": settings.llm_labeling_model_name,
                        "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                        "stream": False,
                        "format": _risk_schema(),
                    },
                )
                response.raise_for_status()
            payload = json.loads(response.json()["message"]["content"])
        else:
            if not settings.openai_api_key:
                return None
            from openai import OpenAI

            response = OpenAI(api_key=settings.openai_api_key).responses.create(
                model=settings.llm_labeling_model_name,
                input=json.dumps(prompt, ensure_ascii=False),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "article_risk_assessment_v1",
                        "strict": True,
                        "schema": _risk_schema(),
                    }
                },
            )
            payload = json.loads(response.output_text)
    except Exception:
        logger.exception("기사 위험 LLM 보완 실패 (article=%s)", article.id)
        return None

    primary = payload.get("primary_type")
    probability = _clamp(payload.get("risk_probability", 0.0))
    has_type = primary in RISK_TYPES
    if bool(payload.get("is_risk")) and has_type and probability >= settings.article_risk_candidate_threshold:
        decision = "risk"
    elif probability < settings.article_risk_uncertain_low or not has_type:
        decision = "non_risk"
    else:
        decision = "uncertain"
    return {
        "decision": decision,
        "risk_probability": round(probability, 6),
        "type_scores": {item: round(_clamp((payload.get("type_scores") or {}).get(item, 0.0)), 6) for item in RISK_TYPES},
        "primary_type": primary if decision != "non_risk" else None,
        "classifier_kind": "rules_nli_llm",
        "model_version": f"{ENGINE_VERSION}:{settings.llm_labeling_model_name}",
        "reason": str(payload.get("reason") or "")[:4000],
    }


def _save_assessment(
    db: Session,
    company: Company,
    article: NewsArticle,
    cluster: StoryClusterArticle,
    settings: Settings,
    *,
    allow_llm: bool,
    relevance_score: float | None = None,
    local_result: dict | None = None,
) -> tuple[ArticleRiskAssessment, bool]:
    filter_result = None if relevance_score is not None else _latest_filter(db, company.id, article.id)
    relevance = _clamp(
        relevance_score
        if relevance_score is not None
        else filter_result.relevance_score
        if filter_result and filter_result.relevance_score is not None
        # A missing filter decision is not evidence of company relevance.
        else 0.0
    )
    assessment = db.get(ArticleRiskAssessment, (company.id, article.id))
    current_llm_version = f"{ENGINE_VERSION}:{settings.llm_labeling_model_name}"
    if (
        assessment is not None
        and assessment.model_version.startswith(ENGINE_VERSION)
        and (
            assessment.model_version == current_llm_version
            or assessment.decision != "uncertain"
            or not allow_llm
        )
    ):
        # Re-clustering changes event identity, not the article prediction. Keep
        # the expensive, still-current assessment and only synchronize its link.
        if assessment.story_cluster_id != cluster.story_cluster_id:
            assessment.story_cluster_id = cluster.story_cluster_id
            db.flush()
        return assessment, False
    result = local_result or _local_assessment(article, relevance, settings)
    attempted_llm = False
    if result["decision"] == "uncertain" and allow_llm:
        attempted_llm = True
        supplemented = _llm_assessment(db, company, article, settings)
        if supplemented is not None:
            result = supplemented
    domain = source_domain(article.original_url or article.url)
    values = {
        **result,
        "story_cluster_id": cluster.story_cluster_id,
        "relevance_score": relevance,
        "source_domain": domain,
        "source_credibility": source_credibility(domain),
        "assessed_at": datetime.now(timezone.utc),
    }
    if assessment is None:
        assessment = ArticleRiskAssessment(company_id=company.id, article_id=article.id, **values)
        db.add(assessment)
    else:
        for key, value in values.items():
            setattr(assessment, key, value)
    db.flush()
    return assessment, attempted_llm


def _event_lock(db: Session, event_key: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": event_key},
        )


def _aggregate_story_event(
    db: Session,
    company_id: int,
    cluster_id: int,
    primary_type: str | None,
    settings: Settings,
) -> tuple[int | None, bool]:
    """Open or update one event per company/story and attach all related sources.

    Only articles individually classified as risk can satisfy the opening
    threshold. Once that threshold is met, every accepted article in the same
    story is retained as evidence so publisher/source counts describe actual
    coverage rather than only the subset whose classifier score crossed 0.65.
    """
    rows = db.execute(
        select(ArticleRiskAssessment, NewsArticle, StoryClusterArticle)
        .join(NewsArticle, NewsArticle.id == ArticleRiskAssessment.article_id)
        .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
        .where(
            ArticleRiskAssessment.company_id == company_id,
            ArticleRiskAssessment.story_cluster_id == cluster_id,
        )
    ).all()
    candidates = [
        row for row in rows
        if row[0].decision == "risk"
        and row[0].risk_probability >= settings.article_risk_candidate_threshold
    ]
    if not candidates:
        return None, False
    candidate_sources = {
        row[0].source_domain for row in candidates if row[0].source_domain != "unknown"
    }
    if not meets_event_threshold(
        [row[0].risk_probability for row in candidates],
        [row[0].source_domain for row in candidates],
        settings,
    ):
        return None, False

    evidence_rows = [
        row for row in rows
        if row[0].decision != "failed"
        and (
            row[0].relevance_score >= settings.article_filter_relevance_accept_threshold
            or row in candidates
        )
    ]
    aggregate_scores = {
        risk_type: max(
            float((row[0].type_scores or {}).get(risk_type, 0.0))
            for row in candidates
        )
        for risk_type in RISK_TYPES
    }
    resolved_primary_type, _ = max(aggregate_scores.items(), key=lambda item: item[1])

    event_key = f"story-v3:{company_id}:{cluster_id}"
    _event_lock(db, event_key)
    event = db.scalar(select(RiskEvent).where(RiskEvent.event_key == event_key).limit(1))
    cluster = db.get(StoryCluster, cluster_id)
    candidate_times = [_article_time(row[1]) for row in candidates]
    evidence_times = [_article_time(row[1]) for row in evidence_rows]
    evidence_time = max(evidence_times)
    latest_window = db.scalar(
        select(CompanyFeatureWindow)
        .where(
            CompanyFeatureWindow.company_id == company_id,
            CompanyFeatureWindow.window_start <= evidence_time,
        )
        .order_by(CompanyFeatureWindow.window_start.desc())
        .limit(1)
    )
    old_probability = float(event.risk_probability or 0.0) if event else 0.0
    old_severity = event.severity if event else None
    old_primary_type = event.primary_type if event else None
    maximum = max(row[0].risk_probability for row in candidates)
    probability = _clamp(
        maximum
        + min(
            0.15,
            0.05 * max(0, len(candidate_sources) - 1)
            + 0.02 * max(0, len(candidates) - 1),
        )
    )
    severity = "critical" if probability >= 0.85 else "warning"
    created = event is None
    primary_candidate = max(candidates, key=lambda row: row[0].risk_probability)
    if event is None:
        event = RiskEvent(
            company_id=company_id,
            article_id=primary_candidate[1].id,
            story_cluster_id=cluster_id,
            event_key=event_key,
            event_source="story_v2",
            feature_window_id=latest_window.id if latest_window else None,
            anomaly_score=float(latest_window.anomaly_score or 0.0) if latest_window else 0.0,
            risk_probability=probability,
            severity=severity,
            status="open",
            primary_type=resolved_primary_type,
            summary=cluster.representative_title if cluster else primary_candidate[1].title,
            model_version=EVENT_ENGINE_VERSION,
            model_state="provisional",
            approval_state="draft",
            opened_at=min(evidence_times),
            last_seen_at=max(evidence_times),
            last_evidence_at=max(evidence_times),
        )
        db.add(event)
        db.flush()
    else:
        event.status = "monitoring" if event.status != "acknowledged" else event.status
        event.closed_at = None
        event.closure_reason = None
        event.risk_probability = max(old_probability, probability)
        event.severity = "critical" if "critical" in {old_severity, severity} else "warning"
        event.article_id = primary_candidate[1].id
        event.story_cluster_id = cluster_id
        event.primary_type = resolved_primary_type
        event.summary = cluster.representative_title if cluster else primary_candidate[1].title
        event.model_version = EVENT_ENGINE_VERSION
        event.opened_at = min(event.opened_at, min(evidence_times))
        event.last_seen_at = max(event.last_seen_at, max(evidence_times))
        event.last_evidence_at = max(event.last_evidence_at or max(evidence_times), max(evidence_times))
        if latest_window is not None:
            event.feature_window_id = latest_window.id
            event.anomaly_score = max(event.anomaly_score, float(latest_window.anomaly_score or 0.0))

    new_official = False
    revision_delta = 0
    for assessment, article, cluster_link in evidence_rows:
        type_match = float((assessment.type_scores or {}).get(resolved_primary_type, 0.0))
        representativeness = 1.0 if cluster_link.is_representative else _clamp(cluster_link.similarity)
        evidence_score = _clamp(
            0.30 * assessment.risk_probability
            + 0.25 * assessment.relevance_score
            + 0.20 * type_match
            + 0.15 * assessment.source_credibility
            + 0.10 * representativeness
        )
        link = db.get(RiskEventArticle, (event.id, article.id))
        if link is None:
            link = RiskEventArticle(risk_event_id=event.id, article_id=article.id)
            db.add(link)
            revision_delta += 1
            new_official = new_official or assessment.source_credibility >= 0.90
        elif abs(float(link.evidence_score or 0.0) - evidence_score) >= 0.05:
            revision_delta += 1
        link.evidence_score = round(evidence_score, 6)
        link.risk_probability = assessment.risk_probability
        link.relevance_score = assessment.relevance_score
        link.type_match_score = type_match
        link.source_credibility = assessment.source_credibility
        link.representativeness = representativeness
    if revision_delta:
        event.evidence_revision += revision_delta

    for risk_type, score in aggregate_scores.items():
        if score < 0.35 and risk_type != resolved_primary_type:
            continue
        link = db.get(RiskEventType, (event.id, risk_type))
        if link is None:
            link = RiskEventType(
                risk_event_id=event.id,
                risk_type=risk_type,
                probability=score,
                evidence={"source": ENGINE_VERSION},
            )
            db.add(link)
        else:
            link.probability = max(link.probability, score)
        link.is_primary = risk_type == resolved_primary_type

    material_change = (
        created
        or (old_severity != "critical" and event.severity == "critical")
        or probability >= old_probability + 0.10
        or old_primary_type != resolved_primary_type
        or new_official
    )
    if material_change and event.evidence_revision > event.last_response_revision:
        event.response_generation_status = "pending"
        event.response_generation_error = None
    db.flush()
    return event.id, material_change and event.evidence_revision > event.last_response_revision


def process_company_risk_articles(
    company_id: int,
    article_ids: list[int] | None = None,
    *,
    enqueue_drafts: bool = True,
    llm_max_attempts: int | None = None,
) -> dict[str, int]:
    """새 정제 기사를 판정하고 조건을 충족한 스토리 사건을 원자적으로 갱신한다."""
    settings = get_settings()
    if not settings.story_risk_engine_enabled:
        return {"assessed": 0, "llm_attempted": 0, "events_changed": 0, "drafts_enqueued": 0}
    enqueue_ids: set[int] = set()
    assessed = 0
    changed = 0
    llm_attempted = 0
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            raise ValueError(f"company {company_id}을(를) 찾을 수 없습니다.")
        _event_lock(db, f"story-risk-company:{company_id}")
        query = (
            select(NewsArticle, StoryClusterArticle)
            .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
            .join(StoryClusterArticle, StoryClusterArticle.article_id == NewsArticle.id)
            .where(CompanyArticleMatch.company_id == company_id)
            .order_by(NewsArticle.id)
        )
        if article_ids is not None:
            if not article_ids:
                return {"assessed": 0, "llm_attempted": 0, "events_changed": 0, "drafts_enqueued": 0}
            query = query.where(NewsArticle.id.in_(set(article_ids)))
        rows = db.execute(query).all()
        touched: set[int] = set()
        llm_remaining = max(
            0,
            settings.article_risk_llm_max_per_run
            if llm_max_attempts is None
            else llm_max_attempts,
        )
        prepared: list[tuple[NewsArticle, StoryClusterArticle, float]] = []
        local_items: list[tuple[NewsArticle, float]] = []
        for article, cluster in rows:
            filter_result = _latest_filter(db, company.id, article.id)
            relevance = _clamp(
                filter_result.relevance_score
                if filter_result and filter_result.relevance_score is not None
                # Legacy/malformed matches without accepted filter evidence must
                # never become risk events solely because another model is high.
                else 0.0
            )
            prepared.append((article, cluster, relevance))
            assessment = db.get(ArticleRiskAssessment, (company.id, article.id))
            current_llm_version = f"{ENGINE_VERSION}:{settings.llm_labeling_model_name}"
            is_current = (
                assessment is not None
                and assessment.model_version.startswith(ENGINE_VERSION)
                and (
                    assessment.model_version == current_llm_version
                    or assessment.decision != "uncertain"
                    or llm_remaining <= 0
                )
            )
            if not is_current:
                local_items.append((article, relevance))
        local_results = _local_assessment_batch(local_items, settings)
        for article, cluster, relevance in prepared:
            assessment, attempted_llm = _save_assessment(
                db,
                company,
                article,
                cluster,
                settings,
                allow_llm=llm_remaining > 0,
                relevance_score=relevance,
                local_result=local_results.get(article.id),
            )
            if attempted_llm:
                llm_remaining -= 1
                llm_attempted += 1
            assessed += 1
            if assessment.decision == "risk" and assessment.primary_type:
                touched.add(assessment.story_cluster_id)
        for cluster_id in sorted(touched):
            event_id, should_generate = _aggregate_story_event(
                db, company_id, cluster_id, None, settings
            )
            if event_id is not None:
                changed += 1
            if event_id is not None and should_generate:
                enqueue_ids.add(event_id)
        db.commit()

    if enqueue_drafts and enqueue_ids:
        from app.services.response_engine import enqueue_response_draft

        for event_id in sorted(enqueue_ids):
            enqueue_response_draft(event_id)
    return {
        "assessed": assessed,
        "llm_attempted": llm_attempted,
        "events_changed": changed,
        "drafts_enqueued": len(enqueue_ids) if enqueue_drafts else 0,
    }


def enqueue_company_risk_articles(company_id: int, article_ids: list[int]) -> None:
    """수집 요청을 막지 않고 기사별 판정과 사건 집계를 실행한다."""
    if not article_ids or not get_settings().story_risk_engine_enabled:
        return

    def _run() -> None:
        try:
            process_company_risk_articles(company_id, article_ids)
        except Exception:
            logger.exception("기사·스토리 위험 집계 실패 (company=%s)", company_id)

    _executor.submit(_run)


def close_stale_story_events(company_id: int | None = None, now: datetime | None = None) -> int:
    """마지막 적격 근거 이후 설정 시간이 지난 활성 story_v2 사건을 닫는다."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.story_event_inactivity_hours)
    with SessionLocal() as db:
        query = select(RiskEvent).where(
            RiskEvent.event_source == "story_v2",
            RiskEvent.status.in_(["open", "monitoring", "acknowledged"]),
            RiskEvent.last_evidence_at.is_not(None),
            RiskEvent.last_evidence_at < cutoff,
        )
        if company_id is not None:
            query = query.where(RiskEvent.company_id == company_id)
        events = list(db.scalars(query))
        for event in events:
            event.status = "closed"
            event.closed_at = now
            event.closure_reason = "evidence_inactive"
            if event.response_generation_status in {"pending", "generating", "deferred"}:
                event.response_generation_status = "idle"
                event.response_generation_error = None
        db.commit()
        return len(events)


def rebuild_recent_story_events(
    company_id: int | None = None,
    hours: int | None = None,
    *,
    all_history: bool = False,
    batch_size: int = 250,
    recluster: bool = False,
    enqueue_drafts: bool = False,
    draft_limit: int | None = None,
) -> dict[str, int | str | bool]:
    """창 기반 사건을 이관하고 선택한 기사 범위를 story_v2 기준으로 재구성한다."""
    settings = get_settings()
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")
    if hours is not None and hours < 1:
        raise ValueError("hours는 1 이상이어야 합니다.")
    if draft_limit is not None and draft_limit < 1:
        raise ValueError("draft_limit은 1 이상이어야 합니다.")
    effective_hours = hours or settings.story_event_rebuild_hours
    cutoff = None if all_history else datetime.now(timezone.utc) - timedelta(hours=effective_hours)
    recluster_result: dict[str, int | bool] = {
        "articles": 0,
        "clusters": 0,
        "links_changed": 0,
        "assessments_changed": 0,
        "semantic_used": False,
    }
    if recluster:
        from app.services.story_clustering import recluster_story_articles

        with SessionLocal() as db:
            _event_lock(db, "story-cluster-v2-rebuild")
            recluster_result = recluster_story_articles(
                db,
                company_id=company_id,
                cutoff=cutoff,
                settings=settings,
            )
            db.commit()
    with SessionLocal() as db:
        legacy_query = select(RiskEvent).where(
            RiskEvent.event_source == "window_v1",
        )
        if all_history:
            legacy_query = legacy_query.where(RiskEvent.status != "legacy_candidate")
        else:
            legacy_query = legacy_query.where(
                RiskEvent.status.in_(["open", "monitoring", "acknowledged"])
            )
        if company_id is not None:
            legacy_query = legacy_query.where(RiskEvent.company_id == company_id)
        legacy = list(db.scalars(legacy_query))
        now = datetime.now(timezone.utc)
        for event in legacy:
            event.status = "legacy_candidate" if all_history else "closed"
            event.closed_at = event.closed_at or now
            event.closure_reason = "story_v2_migration"

        # A v2 re-cluster changes the stable story identity. Preserve the prior
        # story-v2 events for audit, but hide them from operational counts before
        # creating one story-v3 key per newly resolved company/story.
        obsolete_story = []
        if recluster:
            obsolete_query = select(RiskEvent).where(
                RiskEvent.event_source == "story_v2",
                RiskEvent.event_key.like("story-v2:%"),
                RiskEvent.status != "legacy_candidate",
            )
            if company_id is not None:
                obsolete_query = obsolete_query.where(RiskEvent.company_id == company_id)
            if cutoff is not None:
                obsolete_query = obsolete_query.where(RiskEvent.opened_at >= cutoff)
            obsolete_story = list(db.scalars(obsolete_query))
            for event in obsolete_story:
                event.status = "legacy_candidate"
                event.closed_at = event.closed_at or now
                event.closure_reason = "story_cluster_v2_migration"

        company_query = select(CompanyArticleMatch.company_id).join(
            NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id
        )
        if cutoff is not None:
            company_query = company_query.where(
                func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= cutoff
            )
        if company_id is not None:
            company_query = company_query.where(CompanyArticleMatch.company_id == company_id)
        company_ids = sorted(set(db.scalars(company_query)))
        db.commit()

    totals = {
        "scope": "all" if all_history else f"hours:{effective_hours}",
        "legacy_migrated": len(legacy),
        "story_events_migrated": len(obsolete_story),
        "companies": len(company_ids),
        "batches": 0,
        "assessed": 0,
        "llm_attempted": 0,
        "events_changed": 0,
        "stale_closed": 0,
        "drafts_enqueued": 0,
        "drafts_deferred": 0,
        "reclustered_articles": int(recluster_result["articles"]),
        "reclustered_clusters": int(recluster_result["clusters"]),
        "cluster_links_changed": int(recluster_result["links_changed"]),
        "cluster_assessments_changed": int(recluster_result["assessments_changed"]),
        "cluster_semantic_used": bool(recluster_result["semantic_used"]),
    }
    for target_id in company_ids:
        with SessionLocal() as db:
            current_llm_version = f"{ENGINE_VERSION}:{settings.llm_labeling_model_name}"
            llm_used = int(
                db.scalar(
                    select(func.count())
                    .select_from(ArticleRiskAssessment)
                    .where(
                        ArticleRiskAssessment.company_id == target_id,
                        ArticleRiskAssessment.model_version == current_llm_version,
                    )
                )
                or 0
            )
            llm_remaining = max(0, settings.article_risk_llm_max_per_run - llm_used)
            id_query = (
                select(CompanyArticleMatch.article_id)
                .join(NewsArticle, NewsArticle.id == CompanyArticleMatch.article_id)
                .outerjoin(
                    ArticleRiskAssessment,
                    (ArticleRiskAssessment.company_id == CompanyArticleMatch.company_id)
                    & (ArticleRiskAssessment.article_id == CompanyArticleMatch.article_id),
                )
                .where(CompanyArticleMatch.company_id == target_id)
                .order_by(CompanyArticleMatch.article_id)
            )
            reusable = ArticleRiskAssessment.model_version.startswith(ENGINE_VERSION)
            if llm_remaining > 0:
                reusable = reusable & (
                    (ArticleRiskAssessment.decision != "uncertain")
                    | (ArticleRiskAssessment.model_version == current_llm_version)
                )
            id_query = id_query.where(
                (ArticleRiskAssessment.article_id.is_(None)) | ~reusable
            )
            if cutoff is not None:
                id_query = id_query.where(
                    func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= cutoff
                )
            ids = list(db.scalars(id_query))
        for start in range(0, len(ids), batch_size):
            result = process_company_risk_articles(
                target_id,
                ids[start:start + batch_size],
                enqueue_drafts=False,
                llm_max_attempts=llm_remaining,
            )
            llm_remaining = max(0, llm_remaining - result["llm_attempted"])
            totals["batches"] += 1
            for key in ("assessed", "llm_attempted", "events_changed"):
                totals[key] += result[key]
            logger.info(
                "story rebuild progress company=%s processed=%s/%s llm_remaining=%s",
                target_id,
                min(start + batch_size, len(ids)),
                len(ids),
                llm_remaining,
            )

        # Reusable article predictions are intentionally not recomputed during
        # re-clustering, so aggregate every risk-bearing cluster in scope once.
        with SessionLocal() as db:
            cluster_query = (
                select(ArticleRiskAssessment.story_cluster_id)
                .join(NewsArticle, NewsArticle.id == ArticleRiskAssessment.article_id)
                .where(
                    ArticleRiskAssessment.company_id == target_id,
                    ArticleRiskAssessment.decision == "risk",
                )
                .distinct()
                .order_by(ArticleRiskAssessment.story_cluster_id)
            )
            if cutoff is not None:
                cluster_query = cluster_query.where(
                    func.coalesce(NewsArticle.published_at, NewsArticle.created_at) >= cutoff
                )
            for cluster_id in db.scalars(cluster_query):
                event_id, _should_generate = _aggregate_story_event(
                    db,
                    target_id,
                    cluster_id,
                    None,
                    settings,
                )
                if event_id is not None:
                    totals["events_changed"] += 1
            db.commit()
    totals["stale_closed"] = close_stale_story_events(
        company_id=company_id,
        now=datetime.now(timezone.utc),
    )
    with SessionLocal() as db:
        closed_waiting = list(
            db.scalars(
                select(RiskEvent).where(
                    RiskEvent.event_source == "story_v2",
                    RiskEvent.status == "closed",
                    RiskEvent.response_generation_status.in_(["pending", "generating", "deferred"]),
                )
            )
        )
        for event in closed_waiting:
            event.response_generation_status = "idle"
            event.response_generation_error = None
        active_query = select(RiskEvent.id).where(
            RiskEvent.event_source == "story_v2",
            RiskEvent.status.in_(["open", "monitoring", "acknowledged"]),
            RiskEvent.evidence_revision > RiskEvent.last_response_revision,
        ).order_by(
            (RiskEvent.severity == "critical").desc(),
            RiskEvent.risk_probability.desc().nullslast(),
            RiskEvent.last_evidence_at.desc().nullslast(),
        )
        if company_id is not None:
            active_query = active_query.where(RiskEvent.company_id == company_id)
        if enqueue_drafts and draft_limit is not None:
            active_query = active_query.limit(draft_limit)
        active_ids = list(db.scalars(active_query))
        if not enqueue_drafts:
            deferred_events = list(
                db.scalars(
                    select(RiskEvent).where(
                        RiskEvent.id.in_(active_ids),
                        RiskEvent.response_generation_status.in_(["pending", "generating"]),
                    )
                )
            ) if active_ids else []
            for event in deferred_events:
                event.response_generation_status = "deferred"
                event.response_generation_error = None
            totals["drafts_deferred"] = len(deferred_events)
        db.commit()
    if enqueue_drafts and active_ids:
        from app.services.response_engine import enqueue_response_draft

        for event_id in active_ids:
            enqueue_response_draft(event_id)
    totals["drafts_enqueued"] = len(active_ids) if enqueue_drafts else 0
    return totals


__all__ = [
    "close_stale_story_events",
    "enqueue_company_risk_articles",
    "process_company_risk_articles",
    "rebuild_recent_story_events",
    "source_credibility",
    "source_domain",
    "meets_event_threshold",
]
