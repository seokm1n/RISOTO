"""Resolve and apply the authoritative human review for a risk event."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, delete, select
from sqlalchemy.orm import Session

from app.models import (
    NewsArticle,
    ResponseDraft,
    RiskEvent,
    RiskEventArticle,
    RiskEventLabel,
    RiskEventType,
)


CONFIRMED_REVIEW_STATUSES = ("confirmed", "adjudicated")


def authoritative_risk_label(
    db: Session,
    risk_event_id: int,
) -> RiskEventLabel | None:
    """Prefer adjudication, otherwise use the most recently confirmed review."""
    return db.scalar(
        select(RiskEventLabel)
        .where(
            RiskEventLabel.risk_event_id == risk_event_id,
            RiskEventLabel.status.in_(CONFIRMED_REVIEW_STATUSES),
        )
        .order_by(
            case((RiskEventLabel.status == "adjudicated", 0), else_=1),
            RiskEventLabel.reviewed_at.desc(),
            RiskEventLabel.id.desc(),
        )
        .limit(1)
    )


def validate_risk_label_evidence(
    db: Session,
    event: RiskEvent,
    *,
    is_risk: bool,
    event_start: datetime,
    event_end: datetime | None,
    risk_types: list[str],
    evidence_article_ids: list[int],
    status: str,
) -> list[int]:
    """Validate dates and ensure every claimed article is owned by this event."""
    if event_end is not None and event_end < event_start:
        raise ValueError("사건 종료 시각은 시작 시각보다 빠를 수 없습니다.")

    normalized_ids = list(dict.fromkeys(evidence_article_ids))
    if normalized_ids:
        owned_ids = set(
            db.scalars(
                select(RiskEventArticle.article_id).where(
                    RiskEventArticle.risk_event_id == event.id,
                    RiskEventArticle.article_id.in_(normalized_ids),
                )
            )
        )
        missing = [article_id for article_id in normalized_ids if article_id not in owned_ids]
        if missing:
            raise ValueError(
                "이 위험 이벤트에 연결되지 않은 근거 기사입니다: "
                + ", ".join(str(article_id) for article_id in missing)
            )

    if status in CONFIRMED_REVIEW_STATUSES and is_risk:
        if not risk_types:
            raise ValueError("확정 위험 사건에는 위험 유형을 하나 이상 지정해야 합니다.")
        if not normalized_ids:
            raise ValueError("확정 위험 사건에는 근거 기사를 하나 이상 지정해야 합니다.")
    if not is_risk and risk_types:
        raise ValueError("정상 사건에는 위험 유형을 지정할 수 없습니다.")
    return normalized_ids


def apply_authoritative_risk_label(
    db: Session,
    event: RiskEvent,
) -> RiskEventLabel | None:
    """Synchronize reviewed truth into event types and evidence used downstream."""
    label = authoritative_risk_label(db, event.id)
    if label is None:
        return None

    db.execute(delete(RiskEventType).where(RiskEventType.risk_event_id == event.id))
    risk_types = list(dict.fromkeys(label.risk_types or []))
    evidence_ids = list(dict.fromkeys(label.evidence_article_ids or []))
    now = datetime.now(timezone.utc)

    if not label.is_risk:
        event.primary_type = None
        event.risk_probability = 0.0
        event.severity = "warning"
        event.status = "dismissed"
        event.closed_at = label.event_end or now
        event.consecutive_below = 0
        for draft in db.scalars(
            select(ResponseDraft).where(
                ResponseDraft.risk_event_id == event.id,
                ResponseDraft.approval_state == "draft",
            )
        ):
            draft.approval_state = "rejected"
            draft.reviewed_by = label.annotator
            draft.reviewed_at = now
            draft.review_notes = "관리에서 정상 사건으로 확정되어 자동 폐기되었습니다."
        return label

    event.primary_type = risk_types[0]
    event.opened_at = label.event_start
    event.closed_at = label.event_end
    event.status = "closed" if label.event_end is not None else "monitoring"
    event.consecutive_below = 0
    if evidence_ids:
        event.article_id = evidence_ids[0]
    for index, risk_type in enumerate(risk_types):
        db.add(
            RiskEventType(
                risk_event_id=event.id,
                risk_type=risk_type,
                probability=1.0,
                is_primary=index == 0,
                evidence={
                    "source": "human_review",
                    "risk_event_label_id": label.id,
                },
            )
        )
    if evidence_ids:
        articles = set(
            db.scalars(select(NewsArticle.id).where(NewsArticle.id.in_(evidence_ids)))
        )
        for link in db.scalars(
            select(RiskEventArticle).where(
                RiskEventArticle.risk_event_id == event.id,
                RiskEventArticle.article_id.in_(articles),
            )
        ):
            link.evidence_score = 1.0
    return label
