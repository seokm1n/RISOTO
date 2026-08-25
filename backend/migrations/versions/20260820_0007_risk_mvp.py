"""Add the 15-minute risk MVP, review, clustering and collection health schema.

Revision ID: 20260820_0007
Revises: 20260813_0006
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0007"
down_revision: Union[str, Sequence[str], None] = "20260813_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamps() -> list[sa.Column]:
    """Return the shared immutable creation/update columns used by ORM mixins."""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    """Create the non-destructive MVP data contract and preserve legacy risk candidates."""
    op.add_column("news_articles", sa.Column("positive_probability", sa.Float(), nullable=True))
    op.add_column("news_articles", sa.Column("neutral_probability", sa.Float(), nullable=True))
    op.add_column("news_articles", sa.Column("negative_probability", sa.Float(), nullable=True))

    op.create_table(
        "collection_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("successful_query_count", sa.Integer(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_collection_attempts_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["collection_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "source", name="uq_collection_attempts_job_source"),
    )
    op.create_index(
        "ix_collection_attempts_company_source_started",
        "collection_attempts",
        ["company_id", "source", "started_at"],
    )

    op.create_table(
        "collection_incidents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("data_quality", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("affected_company_ids", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('open', 'retrying', 'recovered', 'acknowledged')",
            name="ck_collection_incidents_status",
        ),
        sa.CheckConstraint(
            "data_quality IN ('partial', 'unavailable')",
            name="ck_collection_incidents_quality",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_incidents_status_detected",
        "collection_incidents",
        ["status", "detected_at"],
    )
    op.create_index(
        "ix_collection_incidents_fingerprint_window",
        "collection_incidents",
        ["fingerprint", "scheduled_for"],
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("event_kind", sa.String(length=30), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="ck_notification_deliveries_status",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["collection_incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "event_kind", name="uq_notification_delivery_incident_event"),
    )
    op.create_index(
        "ix_notification_deliveries_retry",
        "notification_deliveries",
        ["status", "next_retry_at"],
    )

    op.create_table(
        "article_query_hits",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("raw_article_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("matched_keyword", sa.String(length=200), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["collection_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["raw_article_id"], ["raw_news_articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "raw_article_id", "company_id", "source", "query",
            name="uq_article_query_hits_article_company_source_query",
        ),
    )
    op.create_index(
        "ix_article_query_hits_company_last_seen", "article_query_hits", ["company_id", "last_seen_at"]
    )

    op.create_table(
        "story_clusters",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("representative_title", sa.Text(), nullable=False),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index("ix_story_clusters_last_published", "story_clusters", ["last_published_at"])
    op.create_table(
        "story_cluster_articles",
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("story_cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("is_representative", sa.Boolean(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_cluster_id"], ["story_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_index(
        "ix_story_cluster_articles_cluster", "story_cluster_articles", ["story_cluster_id"]
    )

    op.create_table(
        "article_labels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_article_id", sa.BigInteger(), nullable=False),
        sa.Column("annotator", sa.String(length=100), nullable=False),
        sa.Column("relevance_label", sa.String(length=30), nullable=False),
        sa.Column("advertisement_label", sa.String(length=20), nullable=False),
        sa.Column("sentiment_label", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "relevance_label IN ('relevant', 'incidental', 'irrelevant', 'uncertain')",
            name="ck_article_labels_relevance",
        ),
        sa.CheckConstraint(
            "advertisement_label IN ('yes', 'no', 'uncertain')",
            name="ck_article_labels_advertisement",
        ),
        sa.CheckConstraint(
            "sentiment_label IN ('positive', 'neutral', 'negative', 'mixed', 'uncertain', 'not_applicable')",
            name="ck_article_labels_sentiment",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'adjudicated')", name="ck_article_labels_status"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["raw_article_id"], ["raw_news_articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "raw_article_id", "annotator", name="uq_article_labels_annotator"),
    )
    op.create_index("ix_article_labels_status_reviewed", "article_labels", ["status", "reviewed_at"])

    op.create_table(
        "company_feature_windows",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_quality", sa.String(length=20), nullable=False),
        sa.Column("successful_sources", sa.JSON(), nullable=False),
        sa.Column("failed_sources", sa.JSON(), nullable=False),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("story_count", sa.Integer(), nullable=False),
        sa.Column("amplification_count", sa.Integer(), nullable=False),
        sa.Column("publisher_count", sa.Integer(), nullable=False),
        sa.Column("positive_probability", sa.Float(), nullable=True),
        sa.Column("neutral_probability", sa.Float(), nullable=True),
        sa.Column("negative_probability", sa.Float(), nullable=True),
        sa.Column("negative_probability_p90", sa.Float(), nullable=True),
        sa.Column("risk_keyword_count", sa.Integer(), nullable=False),
        sa.Column("risk_keyword_ratio", sa.Float(), nullable=False),
        sa.Column("risk_type_scores", sa.JSON(), nullable=False),
        sa.Column("feature_values", sa.JSON(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=True),
        sa.Column("anomaly_percentile", sa.Float(), nullable=True),
        sa.Column("risk_probability", sa.Float(), nullable=True),
        sa.Column("decision_threshold", sa.Float(), nullable=True),
        sa.Column("is_risk", sa.Boolean(), nullable=False),
        sa.Column("model_state", sa.String(length=20), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.CheckConstraint(
            "data_quality IN ('complete', 'partial', 'unavailable')",
            name="ck_company_feature_windows_quality",
        ),
        sa.CheckConstraint(
            "model_state IN ('production', 'provisional', 'unavailable')",
            name="ck_company_feature_windows_model_state",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "window_start", name="uq_company_feature_windows_company_start"),
    )
    op.create_index("ix_company_feature_windows_start", "company_feature_windows", ["window_start"])

    op.create_table(
        "company_daily_summaries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("story_count", sa.Integer(), nullable=False),
        sa.Column("amplification_count", sa.Integer(), nullable=False),
        sa.Column("publisher_count", sa.Integer(), nullable=False),
        sa.Column("positive_probability", sa.Float(), nullable=True),
        sa.Column("neutral_probability", sa.Float(), nullable=True),
        sa.Column("negative_probability", sa.Float(), nullable=True),
        sa.Column("risk_event_count", sa.Integer(), nullable=False),
        sa.Column("unavailable_window_count", sa.Integer(), nullable=False),
        sa.Column("partial_window_count", sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "summary_date", name="uq_company_daily_summary"),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("base_model", sa.String(length=200), nullable=True),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("training_data_hash", sa.String(length=64), nullable=False),
        sa.Column("label_schema", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("training_counts", sa.JSON(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('candidate', 'production', 'retired')", name="ck_model_versions_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task", "version", name="uq_model_versions_task_version"),
    )
    op.create_index("ix_model_versions_task_status", "model_versions", ["task", "status"])

    op.create_table(
        "case_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risk_types", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "case_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("case_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["case_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "url", name="uq_case_sources_url"),
    )

    # Convert article-centric legacy alerts to preserved candidates, then make
    # risk events window-centric without deleting any existing record.
    op.drop_constraint("uq_risk_events_company_article", "risk_events", type_="unique")
    op.drop_constraint("ck_risk_events_status", "risk_events", type_="check")
    op.drop_constraint("risk_events_article_id_fkey", "risk_events", type_="foreignkey")
    op.alter_column("risk_events", "article_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_foreign_key(
        "fk_risk_events_article_id_v2",
        "risk_events",
        "news_articles",
        ["article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("risk_events", sa.Column("feature_window_id", sa.BigInteger(), nullable=True))
    op.add_column("risk_events", sa.Column("risk_probability", sa.Float(), nullable=True))
    op.add_column("risk_events", sa.Column("primary_type", sa.String(length=40), nullable=True))
    op.add_column("risk_events", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("risk_events", sa.Column("model_version", sa.String(length=100), nullable=True))
    op.add_column(
        "risk_events",
        sa.Column("model_state", sa.String(length=20), server_default="provisional", nullable=False),
    )
    op.add_column(
        "risk_events",
        sa.Column("approval_state", sa.String(length=20), server_default="draft", nullable=False),
    )
    op.add_column(
        "risk_events", sa.Column("consecutive_below", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "risk_events",
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column(
        "risk_events",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column("risk_events", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_risk_events_feature_window_id",
        "risk_events",
        "company_feature_windows",
        ["feature_window_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE risk_events
        SET status = 'legacy_candidate', model_state = 'provisional',
            opened_at = detected_at, last_seen_at = detected_at
        """
    )
    op.create_check_constraint(
        "ck_risk_events_status",
        "risk_events",
        "status IN ('open', 'monitoring', 'closed', 'acknowledged', 'dismissed', 'legacy_candidate')",
    )

    op.create_table(
        "risk_event_labels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("risk_event_id", sa.BigInteger(), nullable=False),
        sa.Column("annotator", sa.String(length=100), nullable=False),
        sa.Column("is_risk", sa.Boolean(), nullable=False),
        sa.Column("event_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risk_types", sa.JSON(), nullable=False),
        sa.Column("evidence_article_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'adjudicated')", name="ck_risk_event_labels_status"
        ),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("risk_event_id", "annotator", name="uq_risk_event_labels_annotator"),
    )
    op.create_table(
        "risk_event_articles",
        sa.Column("risk_event_id", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_score", sa.Float(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("risk_event_id", "article_id"),
    )
    op.create_table(
        "risk_event_types",
        sa.Column("risk_event_id", sa.BigInteger(), nullable=False),
        sa.Column("risk_type", sa.String(length=40), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("risk_event_id", "risk_type"),
    )
    op.create_table(
        "response_drafts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("risk_event_id", sa.BigInteger(), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("evidence_urls", sa.JSON(), nullable=False),
        sa.Column("approval_state", sa.String(length=20), nullable=False),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "approval_state IN ('draft', 'approved', 'rejected')", name="ck_response_drafts_state"
        ),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_response_drafts_event_created", "response_drafts", ["risk_event_id", "created_at"]
    )


def downgrade() -> None:
    """Remove only the MVP extension and restore the legacy event contract."""
    op.drop_index("ix_response_drafts_event_created", table_name="response_drafts")
    op.drop_table("response_drafts")
    op.drop_table("risk_event_types")
    op.drop_table("risk_event_articles")
    op.drop_table("risk_event_labels")
    op.drop_constraint("ck_risk_events_status", "risk_events", type_="check")
    op.execute("UPDATE risk_events SET status = 'new' WHERE status IN ('open', 'monitoring', 'legacy_candidate')")
    op.execute("UPDATE risk_events SET status = 'dismissed' WHERE status = 'closed'")
    op.drop_constraint("fk_risk_events_feature_window_id", "risk_events", type_="foreignkey")
    for column in [
        "closed_at", "last_seen_at", "opened_at", "consecutive_below", "approval_state",
        "model_state", "model_version", "summary", "primary_type", "risk_probability", "feature_window_id",
    ]:
        op.drop_column("risk_events", column)
    op.drop_constraint("fk_risk_events_article_id_v2", "risk_events", type_="foreignkey")
    op.alter_column("risk_events", "article_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key(
        "risk_events_article_id_fkey", "risk_events", "news_articles", ["article_id"], ["id"], ondelete="CASCADE"
    )
    op.create_unique_constraint("uq_risk_events_company_article", "risk_events", ["company_id", "article_id"])
    op.create_check_constraint(
        "ck_risk_events_status", "risk_events", "status IN ('new', 'acknowledged', 'dismissed')"
    )
    op.drop_table("case_sources")
    op.drop_table("case_records")
    op.drop_index("ix_model_versions_task_status", table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_table("company_daily_summaries")
    op.drop_index("ix_company_feature_windows_start", table_name="company_feature_windows")
    op.drop_table("company_feature_windows")
    op.drop_index("ix_article_labels_status_reviewed", table_name="article_labels")
    op.drop_table("article_labels")
    op.drop_index("ix_story_cluster_articles_cluster", table_name="story_cluster_articles")
    op.drop_table("story_cluster_articles")
    op.drop_index("ix_story_clusters_last_published", table_name="story_clusters")
    op.drop_table("story_clusters")
    op.drop_index("ix_article_query_hits_company_last_seen", table_name="article_query_hits")
    op.drop_table("article_query_hits")
    op.drop_index("ix_notification_deliveries_retry", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_collection_incidents_fingerprint_window", table_name="collection_incidents")
    op.drop_index("ix_collection_incidents_status_detected", table_name="collection_incidents")
    op.drop_table("collection_incidents")
    op.drop_index("ix_collection_attempts_company_source_started", table_name="collection_attempts")
    op.drop_table("collection_attempts")
    op.drop_column("news_articles", "negative_probability")
    op.drop_column("news_articles", "neutral_probability")
    op.drop_column("news_articles", "positive_probability")
