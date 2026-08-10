from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    database: str
    database_user: str
    postgres_version: str
    pgvector_version: str


class IndustryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None


class CompanyKeywordCreate(BaseModel):
    keyword_type: Literal["alias", "peer", "product", "risk"]
    value: str = Field(min_length=1, max_length=200)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("키워드는 비어 있을 수 없습니다.")
        return normalized


class CompanyKeywordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    keyword_type: str
    value: str


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    industry_id: int
    ticker: str | None = Field(default=None, max_length=30)
    backfill_days: int = Field(default=7, ge=0, le=3650)
    keywords: list[CompanyKeywordCreate] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("기업명은 비어 있을 수 없습니다.")
        return normalized


class CompanyRead(BaseModel):
    id: int
    name: str
    ticker: str | None
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


class CollectionRequest(BaseModel):
    sources: list[Literal["naver", "tavily"]] = Field(
        default_factory=lambda: ["naver", "tavily"], min_length=1, max_length=2
    )
    max_queries: int = Field(default=5, ge=1, le=20)


class CollectionJobRead(BaseModel):
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


class CollectionJobPage(BaseModel):
    items: list[CollectionJobRead]
    total: int
    page: int
    page_size: int


class NewsArticleRead(BaseModel):
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
    anomaly_score: float | None
    is_anomaly: bool
    created_at: datetime


class NewsArticlePage(BaseModel):
    items: list[NewsArticleRead]
    total: int
    page: int
    page_size: int
    sources: list[str]


class CollectionProviderStatus(BaseModel):
    naver: bool
    tavily: bool
    serpapi: bool
    youtube: bool


class MonitoringSummary(BaseModel):
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


class RiskEventRead(BaseModel):
    id: int
    company_id: int
    article_id: int
    article_title: str
    article_url: str
    anomaly_score: float
    severity: str
    status: str
    detected_at: datetime


class DashboardDailyRead(BaseModel):
    day: datetime
    article_count: int
    positive_count: int
    negative_count: int
    risk_count: int


class DashboardSentimentRead(BaseModel):
    label: str
    count: int


class DashboardCompanyRead(BaseModel):
    id: int
    name: str
    monitoring_status: str
    article_count: int
    negative_count: int
    risk_count: int


class DashboardOverview(BaseModel):
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
