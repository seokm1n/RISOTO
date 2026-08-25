"""RISOTO의 기업 모니터링, 기사 처리 및 위험 분석 영속 모델을 정의한다."""

from datetime import date, datetime

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimestampMixin:
    """생성·수정 시각이 필요한 모델에 공통 타임스탬프 열을 제공한다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(TimestampMixin, Base):
    """로그인 자격 증명과 계정 상태를 저장한다."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """브라우저에 전달한 불투명 세션 토큰의 해시와 CSRF 토큰을 저장한다."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Industry(TimestampMixin, Base):
    """기업을 분류하는 계층형 산업군을 저장한다."""

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
    """모니터링 대상 기업과 수집·분석 진행 상태를 저장한다."""

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
        CheckConstraint(
            "company_role IN ('main', 'competitor')",
            name="ck_companies_company_role",
        ),
        CheckConstraint(
            "company_size_class IN ('small_medium', 'mid_sized', 'large')",
            name="ck_companies_size_class",
        ),
        CheckConstraint(
            "annual_revenue_krw > 0",
            name="ck_companies_positive_annual_revenue",
        ),
        UniqueConstraint(
            "user_id",
            "normalized_name",
            "industry_id",
            name="uq_companies_user_normalized_industry",
        ),
        UniqueConstraint(
            "user_id", "ticker", name="uq_companies_user_ticker"
        ),
        Index(
            "uq_companies_one_main_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("company_role = 'main'"),
        ),
        Index("ix_companies_user_id", "user_id"),
        Index("ix_companies_industry_id", "industry_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(220), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(30), nullable=True)
    company_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="competitor", server_default="competitor"
    )
    annual_revenue_krw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company_size_class: Mapped[str] = mapped_column(String(20), nullable=False)
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


class CompanyKeyword(TimestampMixin, Base):
    """기업별 별칭·제품·위험 검색 키워드를 저장한다."""

    __tablename__ = "company_keywords"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "keyword_type", "value", name="uq_company_keywords_value"
        ),
        CheckConstraint(
            "keyword_type IN ('alias', 'product', 'risk')",
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
    """수집 실행 한 건의 요청 범위, 처리량, 오류와 완료 상태를 기록한다."""

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
        Index("ix_collection_jobs_user_id", "user_id"),
        Index("ix_collection_jobs_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
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


class RawNewsArticle(Base):
    """외부 제공자 응답을 필터링 전에 보존하는 원문 기사 레코드다."""

    __tablename__ = "raw_news_articles"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "normalized_url",
            "content_hash",
            name="uq_raw_news_articles_source_url_content",
        ),
        Index("ix_raw_news_articles_normalized_url", "normalized_url"),
        Index("ix_raw_news_articles_content_hash", "content_hash"),
        Index("ix_raw_news_articles_published_at", "published_at"),
        Index("ix_raw_news_articles_collected_at", "collected_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    original_url: Mapped[str | None] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NewsArticle(Base):
    """필터를 통과해 감성 및 이상 탐지 대상이 된 정제 기사다."""

    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("raw_article_id", name="uq_news_articles_raw_article_id"),
        UniqueConstraint("url", name="uq_news_articles_normalized_url"),
        Index("ix_news_articles_published_at", "published_at"),
        Index("ix_news_articles_source", "source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    raw_article_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "raw_news_articles.id",
            name="fk_news_articles_raw_article_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    original_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    sentiment_label: Mapped[str | None] = mapped_column(String(40))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    positive_probability: Mapped[float | None] = mapped_column(Float)
    neutral_probability: Mapped[float | None] = mapped_column(Float)
    negative_probability: Mapped[float | None] = mapped_column(Float)
    sentiment_model: Mapped[str | None] = mapped_column(String(200))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArticleFilterResult(Base):
    """기업별 원문 기사의 승인·거부 판정과 재현 가능한 근거를 저장한다."""

    __tablename__ = "article_filter_results"
    __table_args__ = (
        UniqueConstraint(
            "raw_article_id",
            "company_id",
            "filter_version",
            name="uq_article_filter_results_raw_company_version",
        ),
        CheckConstraint(
            "decision IN ('accepted', 'rejected', 'review_required')",
            name="ck_article_filter_results_decision",
        ),
        CheckConstraint(
            "reason IN ('duplicate', 'advertisement', 'irrelevant', 'accepted')",
            name="ck_article_filter_results_reason",
        ),
        CheckConstraint(
            "(decision = 'accepted' AND reason IN ('accepted', 'duplicate')) OR "
            "(decision <> 'accepted' AND reason <> 'accepted')",
            name="ck_article_filter_results_decision_reason",
        ),
        CheckConstraint(
            "duplicate_of_raw_id IS NULL OR duplicate_of_raw_id <> raw_article_id",
            name="ck_article_filter_results_duplicate_not_self",
        ),
        CheckConstraint(
            "(reason = 'duplicate' AND duplicate_of_raw_id IS NOT NULL) OR "
            "(reason <> 'duplicate' AND duplicate_of_raw_id IS NULL)",
            name="ck_article_filter_results_duplicate_reason",
        ),
        CheckConstraint(
            "relevance_score IS NULL OR "
            "(relevance_score >= 0 AND relevance_score <= 1)",
            name="ck_article_filter_results_relevance_score",
        ),
        CheckConstraint(
            "advertising_score IS NULL OR "
            "(advertising_score >= 0 AND advertising_score <= 1)",
            name="ck_article_filter_results_advertising_score",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_article_filter_results_confidence",
        ),
        Index(
            "ix_article_filter_results_company_decision_filtered",
            "company_id",
            "decision",
            "filtered_at",
        ),
        Index(
            "ix_article_filter_results_duplicate_of_raw_id",
            "duplicate_of_raw_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    raw_article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("raw_news_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    duplicate_of_raw_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("raw_news_articles.id", ondelete="SET NULL"),
        nullable=True,
    )
    curated_article_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("news_articles.id", ondelete="SET NULL"),
        nullable=True,
    )
    relevance_score: Mapped[float | None] = mapped_column(Float)
    advertising_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    classifier_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    filter_version: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    filtered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyArticleMatch(Base):
    """기업과 정제 기사의 연결 및 기업별 이상 탐지 결과를 저장한다."""

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
    """기업별 정상 패턴 모델과 이상 점수 보정 통계를 보존한다."""

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
    """15분 특징 창에서 감지된 기업 위험 사건과 처리 상태를 기록한다."""

    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'monitoring', 'closed', 'acknowledged', "
            "'dismissed', 'legacy_candidate')",
            name="ck_risk_events_status",
        ),
        Index("ix_risk_events_company_id", "company_id"),
        Index("ix_risk_events_detected_at", "detected_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("news_articles.id", ondelete="SET NULL"), nullable=True
    )
    feature_window_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("company_feature_windows.id", ondelete="SET NULL"),
        nullable=True,
    )
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_probability: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    primary_type: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(100))
    model_state: Mapped[str] = mapped_column(String(20), nullable=False, default="provisional")
    approval_state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    consecutive_below: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CollectionAttempt(Base):
    """수집 작업에서 제공자별 성공 여부와 오류를 감사 가능하게 기록한다."""

    __tablename__ = "collection_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_collection_attempts_status"),
        UniqueConstraint("job_id", "source", name="uq_collection_attempts_job_source"),
        Index("ix_collection_attempts_user_id", "user_id"),
        Index("ix_collection_attempts_company_source_started", "company_id", "source", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("collection_jobs.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionIncident(TimestampMixin, Base):
    """여러 기업에 영향을 줄 수 있는 수집 장애와 재시도·복구 상태를 저장한다."""

    __tablename__ = "collection_incidents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'retrying', 'recovered', 'acknowledged')",
            name="ck_collection_incidents_status",
        ),
        CheckConstraint("data_quality IN ('partial', 'unavailable')", name="ck_collection_incidents_quality"),
        Index("ix_collection_incidents_user_id", "user_id"),
        Index("ix_collection_incidents_status_detected", "status", "detected_at"),
        Index("ix_collection_incidents_fingerprint_window", "fingerprint", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    data_quality: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    affected_company_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_summary: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    """장애 Webhook 전송 결과를 수집 작업과 독립적으로 재시도하기 위해 저장한다."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="ck_notification_deliveries_status",
        ),
        UniqueConstraint("incident_id", "event_kind", name="uq_notification_delivery_incident_event"),
        Index("ix_notification_deliveries_retry", "status", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("collection_incidents.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(30), nullable=False, default="webhook")
    event_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArticleQueryHit(Base):
    """한 URL이 어떤 기업·검색어·제공자에서 발견됐는지 별도로 누적한다."""

    __tablename__ = "article_query_hits"
    __table_args__ = (
        UniqueConstraint(
            "raw_article_id", "company_id", "source", "query",
            name="uq_article_query_hits_article_company_source_query",
        ),
        Index("ix_article_query_hits_company_last_seen", "company_id", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    raw_article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_news_articles.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("collection_jobs.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="company")
    matched_keyword: Mapped[str | None] = mapped_column(String(200))
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StoryCluster(TimestampMixin, Base):
    """내용이 비슷하지만 URL은 다른 기사들을 삭제 없이 하나의 스토리로 묶는다."""

    __tablename__ = "story_clusters"
    __table_args__ = (Index("ix_story_clusters_last_published", "last_published_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    representative_title: Mapped[str] = mapped_column(Text, nullable=False)
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoryClusterArticle(Base):
    """정제 기사를 하나의 대표 스토리 군집에 연결한다."""

    __tablename__ = "story_cluster_articles"
    __table_args__ = (Index("ix_story_cluster_articles_cluster", "story_cluster_id"),)

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("news_articles.id", ondelete="CASCADE"), primary_key=True
    )
    story_cluster_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("story_clusters.id", ondelete="CASCADE"), nullable=False
    )
    similarity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArticleLabel(Base):
    """AI 예측과 분리된 블라인드 기사 사람 라벨을 보존한다."""

    __tablename__ = "article_labels"
    __table_args__ = (
        CheckConstraint(
            "relevance_label IN ('relevant', 'incidental', 'irrelevant', 'uncertain')",
            name="ck_article_labels_relevance",
        ),
        CheckConstraint(
            "advertisement_label IN ('yes', 'no', 'uncertain')",
            name="ck_article_labels_advertisement",
        ),
        CheckConstraint(
            "sentiment_label IN ('positive', 'neutral', 'negative', 'mixed', "
            "'uncertain', 'not_applicable')",
            name="ck_article_labels_sentiment",
        ),
        CheckConstraint(
            "status IN ('draft', 'confirmed', 'adjudicated')",
            name="ck_article_labels_status",
        ),
        UniqueConstraint("company_id", "raw_article_id", "annotator", name="uq_article_labels_annotator"),
        Index("ix_article_labels_status_reviewed", "status", "reviewed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    raw_article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_news_articles.id", ondelete="CASCADE"), nullable=False
    )
    annotator: Mapped[str] = mapped_column(String(100), nullable=False)
    relevance_label: Mapped[str] = mapped_column(String(30), nullable=False)
    advertisement_label: Mapped[str] = mapped_column(String(20), nullable=False)
    sentiment_label: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyFeatureWindow(TimestampMixin, Base):
    """기업별 15분 수집 품질·집계 특징과 공통 모델 추론 결과를 저장한다."""

    __tablename__ = "company_feature_windows"
    __table_args__ = (
        CheckConstraint(
            "data_quality IN ('complete', 'partial', 'unavailable')",
            name="ck_company_feature_windows_quality",
        ),
        CheckConstraint(
            "model_state IN ('production', 'provisional', 'unavailable')",
            name="ck_company_feature_windows_model_state",
        ),
        UniqueConstraint("company_id", "window_start", name="uq_company_feature_windows_company_start"),
        Index("ix_company_feature_windows_start", "window_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(20), nullable=False)
    successful_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    failed_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    story_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amplification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publisher_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positive_probability: Mapped[float | None] = mapped_column(Float)
    neutral_probability: Mapped[float | None] = mapped_column(Float)
    negative_probability: Mapped[float | None] = mapped_column(Float)
    negative_probability_p90: Mapped[float | None] = mapped_column(Float)
    risk_keyword_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_keyword_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_type_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    feature_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    anomaly_percentile: Mapped[float | None] = mapped_column(Float)
    risk_probability: Mapped[float | None] = mapped_column(Float)
    decision_threshold: Mapped[float | None] = mapped_column(Float)
    is_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_state: Mapped[str] = mapped_column(String(20), nullable=False, default="unavailable")
    model_version: Mapped[str | None] = mapped_column(String(100))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyDailySummary(TimestampMixin, Base):
    """15분 특징 창을 기업·일 단위로 재집계한 대시보드 지표다."""

    __tablename__ = "company_daily_summaries"
    __table_args__ = (UniqueConstraint("company_id", "summary_date", name="uq_company_daily_summary"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    summary_date: Mapped[date] = mapped_column(nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    story_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amplification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publisher_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positive_probability: Mapped[float | None] = mapped_column(Float)
    neutral_probability: Mapped[float | None] = mapped_column(Float)
    negative_probability: Mapped[float | None] = mapped_column(Float)
    risk_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unavailable_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partial_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RiskEventLabel(Base):
    """감성 대신 사건 자체를 정답으로 삼는 사람 위험 라벨이다."""

    __tablename__ = "risk_event_labels"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'confirmed', 'adjudicated')", name="ck_risk_event_labels_status"),
        UniqueConstraint("risk_event_id", "annotator", name="uq_risk_event_labels_annotator"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    risk_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False
    )
    annotator: Mapped[str] = mapped_column(String(100), nullable=False)
    is_risk: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_article_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RiskEventArticle(Base):
    """위험 사건과 근거 기사·스토리의 다대다 연결이다."""

    __tablename__ = "risk_event_articles"

    risk_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("news_articles.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RiskEventType(Base):
    """위험 사건의 다중 유형과 유형별 신뢰도를 저장한다."""

    __tablename__ = "risk_event_types"

    risk_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True
    )
    risk_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ModelVersion(TimestampMixin, Base):
    """훈련 산출물·평가지표·승격 상태를 재현 가능한 버전으로 관리한다."""

    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint("status IN ('candidate', 'production', 'retired')", name="ck_model_versions_status"),
        UniqueConstraint("task", "version", name="uq_model_versions_task_version"),
        Index("ix_model_versions_task_status", "task", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    base_model: Mapped[str | None] = mapped_column(String(200))
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    training_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    training_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dependencies: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelOperationCheck(TimestampMixin, Base):
    """매일 수행한 수집 품질·라벨 분포·특징 드리프트 점검을 보존한다."""

    __tablename__ = "model_operation_checks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('stable', 'warning', 'insufficient_data')",
            name="ck_model_operation_checks_status",
        ),
        UniqueConstraint("check_date", name="uq_model_operation_checks_date"),
        Index("ix_model_operation_checks_checked_at", "checked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    check_date: Mapped[date] = mapped_column(nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class CaseRecord(TimestampMixin, Base):
    """검증된 과거 위험 사례와 대응 결과를 검색 가능한 형태로 보존한다."""

    __tablename__ = "case_records"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    organization: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    actions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    outcome: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")


class CaseSource(Base):
    """사례의 주장과 직접 연결되는 원문 출처를 저장한다."""

    __tablename__ = "case_sources"
    __table_args__ = (UniqueConstraint("case_id", "url", name="uq_case_sources_url"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("case_records.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")


class ResponseDraft(TimestampMixin, Base):
    """근거 인용이 포함된 위험 대응 초안과 관리자 승인 상태를 보존한다."""

    __tablename__ = "response_drafts"
    __table_args__ = (
        CheckConstraint("approval_state IN ('draft', 'approved', 'rejected')", name="ck_response_drafts_state"),
        CheckConstraint(
            "generation_kind IS NULL OR generation_kind IN ('main_response', 'competitor_impact')",
            name="ck_response_drafts_generation_kind",
        ),
        CheckConstraint("schema_version >= 1", name="ck_response_drafts_schema_version"),
        Index("ix_response_drafts_event_created", "risk_event_id", "created_at"),
        Index("ix_response_drafts_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    risk_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_company_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    target_main_company_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    generation_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    approval_state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(320))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
