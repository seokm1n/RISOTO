"""Add continuous monitoring and ML analysis state.

Revision ID: 20260806_0004
Revises: 20260806_0003
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0004"
down_revision: Union[str, Sequence[str], None] = "20260806_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """감성 분석, 기준선 학습 및 위험 이벤트 모니터링 구조를 추가한다."""
    op.add_column("companies", sa.Column("normalized_name", sa.String(length=220), nullable=True))
    op.add_column(
        "companies",
        sa.Column("analysis_status", sa.String(length=20), server_default="pending", nullable=False),
    )
    op.add_column("companies", sa.Column("analysis_error", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("monitoring_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("baseline_ready_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE companies SET normalized_name = lower(regexp_replace(name, '[[:space:]·._-]+', '', 'g'))"
    )
    op.execute("UPDATE companies SET backfill_days = 7 WHERE backfill_days IN (180, 365)")
    op.alter_column("companies", "normalized_name", nullable=False)
    op.alter_column("companies", "analysis_status", server_default=None)
    op.drop_constraint("companies_name_key", "companies", type_="unique")
    op.drop_constraint("ck_companies_monitoring_status", "companies", type_="check")
    op.create_check_constraint(
        "ck_companies_monitoring_status",
        "companies",
        "monitoring_status IN ('backfilling', 'warming', 'active', 'paused', 'archived', 'error')",
    )
    op.create_check_constraint(
        "ck_companies_analysis_status",
        "companies",
        "analysis_status IN ('pending', 'running', 'warming', 'ready', 'error')",
    )
    op.create_unique_constraint(
        "uq_companies_normalized_industry", "companies", ["normalized_name", "industry_id"]
    )

    op.add_column(
        "collection_jobs",
        sa.Column("job_type", sa.String(length=30), server_default="manual", nullable=False),
    )
    op.add_column("collection_jobs", sa.Column("requested_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("collection_jobs", sa.Column("requested_to", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("collection_jobs", "job_type", server_default=None)
    op.create_check_constraint(
        "ck_collection_jobs_type",
        "collection_jobs",
        "job_type IN ('manual', 'backfill', 'keyword_backfill', 'realtime')",
    )

    op.add_column("news_articles", sa.Column("sentiment_label", sa.String(length=40), nullable=True))
    op.add_column("news_articles", sa.Column("sentiment_score", sa.Float(), nullable=True))
    op.add_column("news_articles", sa.Column("sentiment_confidence", sa.Float(), nullable=True))
    op.add_column("news_articles", sa.Column("sentiment_model", sa.String(length=200), nullable=True))
    op.add_column("news_articles", sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("company_article_matches", sa.Column("anomaly_score", sa.Float(), nullable=True))
    op.add_column(
        "company_article_matches",
        sa.Column("is_anomaly", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "company_article_matches", sa.Column("anomaly_scored_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column("company_article_matches", "is_anomaly", server_default=None)

    op.create_table(
        "company_baselines",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("model_type", sa.String(length=40), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("model_text", sa.Text(), nullable=False),
        sa.Column("feature_names", sa.JSON(), nullable=False),
        sa.Column("training_article_count", sa.Integer(), nullable=False),
        sa.Column("training_day_count", sa.Integer(), nullable=False),
        sa.Column("residual_mean", sa.Float(), nullable=False),
        sa.Column("residual_std", sa.Float(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id"),
    )

    op.create_table(
        "risk_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'acknowledged', 'dismissed')", name="ck_risk_events_status"
        ),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "article_id", name="uq_risk_events_company_article"),
    )
    op.create_index("ix_risk_events_company_id", "risk_events", ["company_id"])
    op.create_index("ix_risk_events_detected_at", "risk_events", ["detected_at"])


def downgrade() -> None:
    """모니터링 파이프라인에서 추가한 데이터베이스 구조를 제거한다."""
    op.drop_index("ix_risk_events_detected_at", table_name="risk_events")
    op.drop_index("ix_risk_events_company_id", table_name="risk_events")
    op.drop_table("risk_events")
    op.drop_table("company_baselines")
    op.drop_column("company_article_matches", "anomaly_scored_at")
    op.drop_column("company_article_matches", "is_anomaly")
    op.drop_column("company_article_matches", "anomaly_score")
    op.drop_column("news_articles", "analyzed_at")
    op.drop_column("news_articles", "sentiment_model")
    op.drop_column("news_articles", "sentiment_confidence")
    op.drop_column("news_articles", "sentiment_score")
    op.drop_column("news_articles", "sentiment_label")
    op.drop_constraint("ck_collection_jobs_type", "collection_jobs", type_="check")
    op.drop_column("collection_jobs", "requested_to")
    op.drop_column("collection_jobs", "requested_from")
    op.drop_column("collection_jobs", "job_type")
    op.drop_constraint("uq_companies_normalized_industry", "companies", type_="unique")
    op.drop_constraint("ck_companies_analysis_status", "companies", type_="check")
    op.drop_constraint("ck_companies_monitoring_status", "companies", type_="check")
    op.create_check_constraint(
        "ck_companies_monitoring_status",
        "companies",
        "monitoring_status IN ('active', 'paused', 'archived')",
    )
    op.create_unique_constraint("companies_name_key", "companies", ["name"])
    op.drop_column("companies", "baseline_ready_at")
    op.drop_column("companies", "last_collected_at")
    op.drop_column("companies", "monitoring_started_at")
    op.drop_column("companies", "analysis_error")
    op.drop_column("companies", "analysis_status")
    op.drop_column("companies", "normalized_name")
