"""API 요청 검증과 응답 직렬화에 사용하는 Pydantic 스키마 모음."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_MAX_REVENUE_100M_KRW = Decimal("92233720368.54")


def _validate_revenue_100m(value: object) -> Decimal:
    """억원 문자열을 소수 둘째 자리까지의 양수 Decimal로 검증한다."""
    raw = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        raise ValueError("연매출은 억원 단위로 소수 둘째 자리까지 입력해 주세요.")
    try:
        revenue = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("올바른 연매출을 입력해 주세요.") from exc
    if revenue <= 0:
        raise ValueError("연매출은 0보다 커야 합니다.")
    if revenue > _MAX_REVENUE_100M_KRW:
        raise ValueError("연매출이 저장 가능한 범위를 초과했습니다.")
    return revenue


class HealthResponse(BaseModel):
    """애플리케이션과 데이터베이스 의존성의 상태 응답."""

    status: str
    app: str
    environment: str
    database: str
    database_user: str
    postgres_version: str
    pgvector_version: str


class AuthSignupRequest(BaseModel):
    """새 사용자 계정을 만드는 회원가입 요청."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("올바른 이메일 주소를 입력해 주세요.")
        return normalized

class AuthLoginRequest(BaseModel):
    """이메일과 비밀번호 로그인 요청."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class AuthPasswordChangeRequest(BaseModel):
    """현재 로그인 사용자의 새 비밀번호와 확인값을 검증한다."""

    new_password: str = Field(min_length=8, max_length=128)
    new_password_confirmation: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_password_confirmation(self):
        if self.new_password != self.new_password_confirmation:
            raise ValueError("새 비밀번호와 비밀번호 확인이 일치하지 않습니다.")
        return self


class AuthUserRead(BaseModel):
    id: int
    email: str


class AuthMeRead(BaseModel):
    user: AuthUserRead
    has_main_company: bool
    csrf_token: str


class IndustryRead(BaseModel):
    """클라이언트에 노출하는 산업군 정보."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None


class CompanyKeywordCreate(BaseModel):
    """기업 등록 시 입력받는 유형별 검색 키워드."""

    keyword_type: Literal["alias", "product", "risk"]
    value: str = Field(min_length=1, max_length=200)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        """키워드의 연속 공백을 정리하고 빈 값 입력을 거부한다."""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("키워드는 비어 있을 수 없습니다.")
        return normalized


class CompanyKeywordRead(BaseModel):
    """저장된 기업 검색 키워드의 응답 표현."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    keyword_type: str
    value: str


class CompanyCreate(BaseModel):
    """기업 생성 또는 기존 기업 보강 요청의 입력값."""

    name: str = Field(min_length=1, max_length=200)
    industry_id: int
    ticker: str | None = Field(default=None, max_length=30)
    annual_revenue_100m_krw: Decimal
    company_size_class: Literal["small_medium", "mid_sized", "large"]
    backfill_days: int = Field(default=7, ge=0, le=3650)
    keywords: list[CompanyKeywordCreate] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """기업명의 연속 공백을 정리하고 빈 값 입력을 거부한다."""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("기업명은 비어 있을 수 없습니다.")
        return normalized

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(value.split()).upper()
        return normalized or None

    @field_validator("annual_revenue_100m_krw", mode="before")
    @classmethod
    def validate_annual_revenue(cls, value: object) -> Decimal:
        return _validate_revenue_100m(value)


class CompanyUpdate(BaseModel):
    """기업 기본 정보와 전체 검색 키워드를 교체하는 수정 요청의 입력값."""

    name: str = Field(min_length=1, max_length=200)
    industry_id: int
    ticker: str | None = Field(default=None, max_length=30)
    annual_revenue_100m_krw: Decimal
    company_size_class: Literal["small_medium", "mid_sized", "large"]
    keywords: list[CompanyKeywordCreate] = Field(max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """수정된 기업명의 연속 공백을 정리하고 빈 값 입력을 거부한다."""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("기업명은 비어 있을 수 없습니다.")
        return normalized

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        """선택 입력인 종목코드의 공백을 제거하고 대문자로 통일한다."""
        if value is None:
            return None
        normalized = "".join(value.split()).upper()
        return normalized or None

    @field_validator("annual_revenue_100m_krw", mode="before")
    @classmethod
    def validate_annual_revenue(cls, value: object) -> Decimal:
        return _validate_revenue_100m(value)


class CompanyRead(BaseModel):
    """키워드와 모니터링 상태를 포함한 기업 상세 응답."""

    id: int
    user_id: int
    name: str
    ticker: str | None
    company_role: Literal["main", "competitor"]
    annual_revenue_100m_krw: Decimal
    company_size_class: Literal["small_medium", "mid_sized", "large"]
    industry_id: int | None
    industry_name: str | None
    backfill_days: int
    monitoring_status: str
    analysis_status: str
    analysis_error: str | None
    monitoring_started_at: datetime | None
    last_collected_at: datetime | None
    baseline_ready_at: datetime | None
    keywords: list[CompanyKeywordRead]
    created_at: datetime
    is_existing: bool = False
    added_keyword_count: int = 0
    readiness_status: Literal["preparing", "pending_approval", "active"] = "preparing"
    accepted_article_count: int = 0
    valid_nonempty_window_count: int = 0
    activation_required: bool = False
    model_state: Literal["production", "provisional", "unavailable"] = "unavailable"


class CollectionRequest(BaseModel):
    """수동 기사 수집에 사용할 제공자와 최대 검색어 수."""

    sources: list[Literal["naver", "tavily", "kakao", "youtube"]] = Field(
        default_factory=lambda: ["naver", "tavily", "kakao", "youtube"],
        min_length=1,
        max_length=4,
    )
    max_queries: int = Field(default=10, ge=1, le=20)


class CollectionJobRead(BaseModel):
    """수집 작업의 범위, 처리량, 오류 및 완료 상태 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    status: str
    job_type: str
    sources: list[str]
    query_count: int
    fetched_count: int
    new_count: int
    matched_count: int
    errors: list[dict]
    requested_from: datetime | None
    requested_to: datetime | None
    started_at: datetime
    completed_at: datetime | None

    @field_validator("errors", mode="before")
    @classmethod
    def sanitize_public_job_errors(cls, value: list[dict] | None) -> list[dict]:
        """Do not expose provider credentials or database diagnostics in job APIs."""
        from app.services.collection_health import sanitize_error

        sanitized: list[dict] = []
        for item in value or []:
            public_item = dict(item)
            if "message" in public_item:
                public_item["message"] = sanitize_error(public_item["message"])
            sanitized.append(public_item)
        return sanitized


class CollectionJobPage(BaseModel):
    """페이지 정보가 포함된 수집 작업 목록."""

    items: list[CollectionJobRead]
    total: int
    page: int
    page_size: int


class NewsArticleRead(BaseModel):
    """감성과 이상 탐지 결과를 포함한 정제 기사 응답."""

    id: int
    source: str
    title: str
    summary: str | None
    url: str
    original_url: str | None
    published_at: datetime | None
    matched_keyword: str | None
    sentiment_label: str | None
    sentiment_score: float | None
    sentiment_confidence: float | None
    positive_probability: float | None = None
    neutral_probability: float | None = None
    negative_probability: float | None = None
    story_cluster_id: int | None = None
    query_hit_count: int = 0
    anomaly_score: float | None
    is_anomaly: bool
    created_at: datetime


class NewsArticlePage(BaseModel):
    """출처 선택값과 페이지 정보가 포함된 기사 목록."""

    items: list[NewsArticleRead]
    total: int
    page: int
    page_size: int
    sources: list[str]


class CollectionProviderStatus(BaseModel):
    """외부 제공자별 자격 증명 구성 여부이며 SerpAPI는 향후 연동 예약 항목이다."""

    naver: bool
    tavily: bool
    kakao: bool
    serpapi: bool
    youtube: bool


class MonitoringSummary(BaseModel):
    """기업 한 곳의 수집·분석·기준선 학습 진행 요약."""

    company_id: int
    monitoring_status: str
    analysis_status: str
    article_count: int
    analyzed_count: int
    anomaly_count: int
    last_collected_at: datetime | None
    baseline_ready_at: datetime | None
    baseline_training_articles: int | None
    baseline_training_days: int | None
    collection_interval_seconds: int
    next_collection_at: datetime | None
    readiness_status: Literal["preparing", "pending_approval", "active"] = "preparing"
    accepted_article_count: int = 0
    valid_nonempty_window_count: int = 0
    data_quality: Literal["complete", "partial", "unavailable"] | None = None
    model_state: Literal["production", "provisional", "unavailable"] = "unavailable"


class ArticleFilterSummary(BaseModel):
    """기업별 최신 기사 필터 판정을 사유와 처리 방식별로 집계한 응답."""

    company_id: int
    raw_count: int
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    advertisement_count: int
    irrelevant_count: int
    review_required_count: int
    ai_assisted_count: int
    rules_only_count: int
    last_filtered_at: datetime | None


class ArticleFilterResultRead(BaseModel):
    """필터 판정 점수와 감사 근거를 포함한 원문 기사 결과."""

    id: int
    raw_article_id: int
    curated_article_id: int | None
    source: str
    title: str
    url: str
    decision: str
    reason: str
    relevance_score: float | None
    advertising_score: float | None
    confidence: float | None
    classifier_kind: str
    filter_version: str
    details: dict
    filtered_at: datetime


class ArticleFilterResultPage(BaseModel):
    """페이지 정보가 포함된 기사 필터 결과 목록."""

    items: list[ArticleFilterResultRead]
    total: int
    page: int
    page_size: int


class BulkMonitoringStateResponse(BaseModel):
    """전체 기업 모니터링 상태 변경의 처리 결과."""

    action: Literal["pause", "resume"]
    monitoring_status: str
    updated_count: int
    total_count: int


class RiskEventRead(BaseModel):
    """15분 위험 사건, 다중 유형 및 여러 근거 기사를 포함한 응답."""

    id: int
    company_id: int
    article_id: int | None = None
    article_title: str | None = None
    article_url: str | None = None
    feature_window_id: int | None = None
    anomaly_score: float
    risk_probability: float | None = None
    severity: str
    status: str
    primary_type: str | None = None
    risk_types: list[dict] = Field(default_factory=list)
    evidence_articles: list[dict] = Field(default_factory=list)
    summary: str | None = None
    model_version: str | None = None
    model_state: str = "provisional"
    approval_state: str = "draft"
    opened_at: datetime | None = None
    last_seen_at: datetime | None = None
    closed_at: datetime | None = None
    detected_at: datetime


class FeatureWindowRead(BaseModel):
    """기업의 수집 품질·집계 지표·공통 모델 결과를 나타내는 15분 창."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    window_start: datetime
    window_end: datetime
    data_quality: Literal["complete", "partial", "unavailable"]
    successful_sources: list[str]
    failed_sources: list[str]
    article_count: int
    story_count: int
    amplification_count: int
    publisher_count: int
    positive_probability: float | None
    neutral_probability: float | None
    negative_probability: float | None
    negative_probability_p90: float | None
    risk_keyword_count: int
    risk_keyword_ratio: float
    risk_type_scores: dict
    feature_values: dict
    anomaly_score: float | None
    anomaly_percentile: float | None
    risk_probability: float | None
    decision_threshold: float | None
    is_risk: bool
    model_state: str
    model_version: str | None
    scored_at: datetime | None


class DailySummaryRead(BaseModel):
    """15분 창으로부터 재구성한 기업별 서울 기준 일일 요약."""

    model_config = ConfigDict(from_attributes=True)

    company_id: int
    summary_date: date
    article_count: int
    story_count: int
    amplification_count: int
    publisher_count: int
    positive_probability: float | None
    neutral_probability: float | None
    negative_probability: float | None
    risk_event_count: int
    unavailable_window_count: int
    partial_window_count: int


class CollectionIncidentRead(BaseModel):
    """대시보드와 Webhook에서 공통으로 사용하는 수집 장애 계약."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: Literal["open", "retrying", "recovered", "acknowledged"]
    data_quality: Literal["partial", "unavailable"]
    severity: str
    scheduled_for: datetime
    detected_at: datetime
    last_seen_at: datetime
    affected_company_ids: list[int]
    sources: list[str]
    error_summary: str
    retry_count: int
    next_retry_at: datetime | None
    notified_at: datetime | None
    recovered_at: datetime | None
    acknowledged_at: datetime | None

    @field_validator("error_summary", mode="before")
    @classmethod
    def sanitize_public_error_summary(cls, value: str) -> str:
        # Existing rows may predate sanitization; public serialization must
        # still never reveal SQL text or bound parameter values.
        from app.services.collection_health import sanitize_error

        return sanitize_error(value)


class CollectionIncidentPage(BaseModel):
    items: list[CollectionIncidentRead]
    total: int
    page: int
    page_size: int


class CollectionSourceHealthRead(BaseModel):
    source: str
    status: Literal["healthy", "partial", "down", "unknown"]
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int


class CollectionHealthRead(BaseModel):
    status: Literal["healthy", "degraded", "unavailable", "unknown"]
    open_incident_count: int
    sources: list[CollectionSourceHealthRead]


class ArticleReviewCandidate(BaseModel):
    """AI 값을 노출하지 않는 블라인드 기사 관리 후보."""

    company_id: int
    company_name: str
    raw_article_id: int
    source: str
    title: str
    summary: str | None
    url: str
    published_at: datetime | None
    review_priority: float


class ArticleLabelCreate(BaseModel):
    company_id: int
    raw_article_id: int
    relevance_label: Literal["relevant", "incidental", "irrelevant", "uncertain"]
    advertisement_label: Literal["yes", "no", "uncertain"]
    sentiment_label: Literal[
        "positive", "neutral", "negative", "mixed", "uncertain", "not_applicable"
    ]
    status: Literal["draft", "confirmed", "adjudicated"] = "confirmed"
    notes: str = Field(default="", max_length=4000)


class ArticleLabelRead(ArticleLabelCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reviewed_at: datetime


class RiskEventLabelCreate(BaseModel):
    is_risk: bool
    event_start: datetime
    event_end: datetime | None = None
    risk_types: list[Literal[
        "product_quality", "safety_accident", "security_privacy", "legal_regulatory",
        "labor_hr", "financial_governance", "supply_operations", "reputation_consumer"
    ]] = Field(default_factory=list)
    evidence_article_ids: list[int] = Field(default_factory=list)
    status: Literal["draft", "confirmed", "adjudicated"] = "confirmed"
    notes: str = Field(default="", max_length=4000)


class RiskEventLabelRead(RiskEventLabelCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    risk_event_id: int
    reviewed_at: datetime


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task: str
    version: str
    status: str
    base_model: str | None
    artifact_path: str
    training_data_hash: str
    label_schema: dict
    metrics: dict
    thresholds: dict
    training_counts: dict
    dependencies: dict
    promoted_at: datetime | None
    retired_at: datetime | None
    created_at: datetime


class RiskDetectionStatusRead(BaseModel):
    """Global availability of the production LightGBM final-risk judgment."""

    risk_detection_status: Literal["available", "unavailable"]
    reason: Literal[
        "production_lightgbm_not_registered",
        "artifact_unavailable",
        "artifact_contract_invalid",
        "production_isolation_forest_not_registered",
        "isolation_artifact_unavailable",
        "isolation_artifact_contract_invalid",
        "isolation_dependency_manifest_invalid",
        "isolation_dependency_mismatch",
    ] | None = None
    message: str
    model_id: int | None = None
    model_version: str | None = None
    model_state: Literal["production", "provisional", "unavailable"]


class ModelRuntimeStatusRead(BaseModel):
    """로컬 텍스트 모델과 위험 판정 런타임의 연결 상태를 노출한다."""

    article_filter_version: str
    article_filter_ai_enabled: bool
    relevance_model_name: str | None = None
    relevance_model_available: bool
    sentiment_model_name: str | None = None
    sentiment_model_available: bool
    external_lightgbm_model_name: str | None = None
    external_lightgbm_model_available: bool
    external_lightgbm_message: str


class NotificationItemRead(BaseModel):
    """Unified, non-personal notice for an open risk or promotable model."""

    id: str
    type: Literal["risk", "model_promotion_ready"]
    title: str
    message: str
    created_at: datetime
    company_id: int | None = None
    risk_event_id: int | None = None
    model_id: int | None = None
    model_task: str | None = None


class NotificationListRead(BaseModel):
    items: list[NotificationItemRead]
    total: int
    risk_count: int
    model_promotion_count: int


class TrainingTaskReadiness(BaseModel):
    """후보 학습 조건을 작업별로 설명하되 학습을 직접 시작하지 않는다."""

    task: Literal[
        "article_filter", "sentiment", "risk_type_classifier", "risk_detector"
    ]
    latest_model_version: str | None
    latest_model_status: str | None
    confirmed_total: int
    new_since_latest: int
    increment_required: int
    class_counts: dict[str, int]
    minimums_met: bool
    increment_met: bool
    candidate_training_ready: bool
    blockers: list[str]
    trainer_command: str


class LlmLabelingAuditRead(BaseModel):
    """이번 달 사람 표본 검수 진행 상황과 LLM 일치율."""

    month: str
    target_sample_size: int
    reviewed_count: int
    agreement_count: int
    agreement_rate: float | None


class LlmLabelingStatusRead(BaseModel):
    """LLM 자동 라벨링 가동 현황과 밀린 기사 수를 보여주는 읽기 전용 요약."""

    enabled: bool
    model_name: str
    llm_labeled_total: int
    llm_labeled_last_24h: int
    pending_backlog: int
    audit: LlmLabelingAuditRead


class ModelTrainingReadinessRead(BaseModel):
    checked_at: datetime
    article_labels_total: int
    risk_event_labels_total: int
    tasks: list[TrainingTaskReadiness]


class ModelOperationCheckRead(BaseModel):
    """서울 날짜별로 보존한 품질·라벨·드리프트 점검 결과다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    check_date: date
    checked_at: datetime
    status: Literal["stable", "warning", "insufficient_data"]
    report: dict
    created_at: datetime
    updated_at: datetime


class ResponseDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    risk_event_id: int
    user_id: int
    source_company_id: int | None = None
    target_main_company_id: int | None = None
    generation_kind: Literal["main_response", "competitor_impact"] | None = None
    schema_version: int = 1
    model_name: str
    content: dict
    evidence_urls: list[str]
    approval_state: Literal["draft", "approved", "rejected"]
    reviewed_by_user_id: int | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None
    review_notes: str
    created_at: datetime


class ResponseDraftReview(BaseModel):
    notes: str = Field(default="", max_length=4000)


class CompanyActivationRead(BaseModel):
    company_id: int
    readiness_status: Literal["preparing", "pending_approval", "active"]
    monitoring_status: str
    activated_at: datetime


class DashboardDailyRead(BaseModel):
    """대시보드 추세 그래프에 사용하는 일별 집계값."""

    day: datetime
    article_count: int
    positive_count: int
    negative_count: int
    risk_count: int


class DashboardSentimentRead(BaseModel):
    """정규화된 감성 레이블별 기사 수."""

    label: str
    count: int


class DashboardCompanyRead(BaseModel):
    """대시보드 기업 표에 사용하는 모니터링 요약값."""

    id: int
    name: str
    company_role: Literal["main", "competitor"]
    annual_revenue_100m_krw: Decimal
    company_size_class: Literal["small_medium", "mid_sized", "large"]
    monitoring_status: str
    article_count: int
    story_count: int = 0
    negative_count: int
    risk_count: int
    readiness_status: str = "preparing"
    data_quality: str | None = None
    model_state: str = "unavailable"


class DashboardOverview(BaseModel):
    """선택 기간의 전체 대시보드 데이터 묶음."""

    days: int
    total_companies: int
    active_companies: int
    article_count: int
    analyzed_count: int
    negative_count: int
    risk_count: int
    daily: list[DashboardDailyRead]
    sentiments: list[DashboardSentimentRead]
    companies: list[DashboardCompanyRead]
    recent_risks: list[RiskEventRead]
    recent_incidents: list[CollectionIncidentRead] = Field(default_factory=list)
