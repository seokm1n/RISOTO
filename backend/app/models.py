from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Industry(TimestampMixin, Base):
    __tablename__ = "industries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("industries.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "monitoring_status IN ('backfilling', 'warming', 'active', 'paused', 'archived', 'error')",
            name="ck_companies_monitoring_status",
        ),
        CheckConstraint(
            "analysis_status IN ('pending', 'running', 'warming', 'ready', 'error')",
            name="ck_companies_analysis_status",
        ),
        CheckConstraint("backfill_days >= 0", name="ck_companies_backfill_days"),
        UniqueConstraint("normalized_name", "industry_id", name="uq_companies_normalized_industry"),
        Index("ix_companies_industry_id", "industry_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(220), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    industry_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("industries.id", ondelete="SET NULL"),
        nullable=True,
    )
    backfill_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    monitoring_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="backfilling"
    )
    analysis_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    analysis_error: Mapped[str | None] = mapped_column(Text)
    monitoring_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_collection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyPeer(TimestampMixin, Base):
    __tablename__ = "company_peers"
    __table_args__ = (
        CheckConstraint("company_id <> peer_company_id", name="ck_company_peers_not_self"),
        CheckConstraint("weight > 0", name="ck_company_peers_positive_weight"),
        Index("ix_company_peers_peer_company_id", "peer_company_id"),
    )

    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    peer_company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class CompanyKeyword(TimestampMixin, Base):
    __tablename__ = "company_keywords"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "keyword_type", "value", name="uq_company_keywords_value"
        ),
        CheckConstraint(
            "keyword_type IN ('alias', 'peer', 'product', 'risk')",
            name="ck_company_keywords_type",
        ),
        Index("ix_company_keywords_company_id", "company_id"),
        Index("ix_company_keywords_value", "value"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    keyword_type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)


class CollectionJob(Base):
    __tablename__ = "collection_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'partial', 'failed')",
            name="ck_collection_jobs_status",
        ),
        CheckConstraint(
            "job_type IN ('manual', 'backfill', 'keyword_backfill', 'realtime')",
            name="ck_collection_jobs_type",
        ),
        Index("ix_collection_jobs_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    job_type: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    requested_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = (
        Index("ix_news_articles_published_at", "published_at"),
        Index("ix_news_articles_source", "source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    original_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    sentiment_label: Mapped[str | None] = mapped_column(String(40))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    sentiment_model: Mapped[str | None] = mapped_column(String(200))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyArticleMatch(Base):
    __tablename__ = "company_article_matches"
    __table_args__ = (
        Index("ix_company_article_matches_article_id", "article_id"),
        Index("ix_company_article_matches_job_id", "job_id"),
    )

    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("news_articles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    job_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("collection_jobs.id", ondelete="SET NULL"),
    )
    matched_keyword: Mapped[str | None] = mapped_column(String(200))
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    anomaly_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyBaseline(Base):
    __tablename__ = "company_baselines"

    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_type: Mapped[str] = mapped_column(String(40), nullable=False, default="lightgbm_regression")
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_text: Mapped[str] = mapped_column(Text, nullable=False)
    feature_names: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    training_article_count: Mapped[int] = mapped_column(Integer, nullable=False)
    training_day_count: Mapped[int] = mapped_column(Integer, nullable=False)
    residual_mean: Mapped[float] = mapped_column(Float, nullable=False)
    residual_std: Mapped[float] = mapped_column(Float, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        UniqueConstraint("company_id", "article_id", name="uq_risk_events_company_article"),
        CheckConstraint(
            "status IN ('new', 'acknowledged', 'dismissed')",
            name="ck_risk_events_status",
        ),
        Index("ix_risk_events_company_id", "company_id"),
        Index("ix_risk_events_detected_at", "detected_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("news_articles.id", ondelete="CASCADE"), nullable=False
    )
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
