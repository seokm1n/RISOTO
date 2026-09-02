"""Add article-level assessments and story-centered risk-event metadata.

Revision ID: 20260902_0023
Revises: 20260901_0022
Create Date: 2026-09-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0023"
down_revision: Union[str, Sequence[str], None] = "20260901_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "article_risk_assessments",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("story_cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("risk_probability", sa.Float(), nullable=False),
        sa.Column("type_scores", sa.JSON(), nullable=False),
        sa.Column("primary_type", sa.String(length=40), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("source_domain", sa.String(length=255), nullable=False),
        sa.Column("source_credibility", sa.Float(), nullable=False),
        sa.Column("classifier_kind", sa.String(length=40), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('risk', 'non_risk', 'uncertain', 'failed')",
            name="ck_article_risk_assessments_decision",
        ),
        sa.CheckConstraint(
            "risk_probability >= 0 AND risk_probability <= 1",
            name="ck_article_risk_assessments_probability",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_cluster_id"], ["story_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", "article_id"),
    )
    op.create_index(
        "ix_article_risk_assessments_story",
        "article_risk_assessments",
        ["company_id", "story_cluster_id"],
    )
    op.create_index(
        "ix_article_risk_assessments_decision",
        "article_risk_assessments",
        ["decision", "assessed_at"],
    )

    op.add_column("risk_events", sa.Column("story_cluster_id", sa.BigInteger(), nullable=True))
    op.add_column("risk_events", sa.Column("event_key", sa.String(length=180), nullable=True))
    op.add_column(
        "risk_events",
        sa.Column("event_source", sa.String(length=20), server_default="window_v1", nullable=False),
    )
    op.add_column(
        "risk_events",
        sa.Column("evidence_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "risk_events",
        sa.Column("last_response_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "risk_events",
        sa.Column("response_generation_status", sa.String(length=20), server_default="idle", nullable=False),
    )
    op.add_column("risk_events", sa.Column("response_generation_error", sa.Text(), nullable=True))
    op.add_column("risk_events", sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("risk_events", sa.Column("closure_reason", sa.String(length=80), nullable=True))
    op.create_foreign_key(
        "fk_risk_events_story_cluster_id",
        "risk_events",
        "story_clusters",
        ["story_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_risk_events_story_cluster_id", "risk_events", ["story_cluster_id"])
    op.create_unique_constraint("uq_risk_events_event_key", "risk_events", ["event_key"])

    for name in (
        "risk_probability",
        "relevance_score",
        "type_match_score",
        "source_credibility",
        "representativeness",
    ):
        op.add_column("risk_event_articles", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in (
        "representativeness",
        "source_credibility",
        "type_match_score",
        "relevance_score",
        "risk_probability",
    ):
        op.drop_column("risk_event_articles", name)
    op.drop_constraint("uq_risk_events_event_key", "risk_events", type_="unique")
    op.drop_index("ix_risk_events_story_cluster_id", table_name="risk_events")
    op.drop_constraint("fk_risk_events_story_cluster_id", "risk_events", type_="foreignkey")
    for name in (
        "closure_reason",
        "last_evidence_at",
        "last_response_revision",
        "response_generation_status",
        "response_generation_error",
        "evidence_revision",
        "event_source",
        "event_key",
        "story_cluster_id",
    ):
        op.drop_column("risk_events", name)
    op.drop_index("ix_article_risk_assessments_decision", table_name="article_risk_assessments")
    op.drop_index("ix_article_risk_assessments_story", table_name="article_risk_assessments")
    op.drop_table("article_risk_assessments")
