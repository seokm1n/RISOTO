"""Persist independent story IF + LightGBM predictions and their frozen inputs."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0025"
down_revision = "20260904_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "story_risk_scores",
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("story_cluster_id", sa.BigInteger(), sa.ForeignKey("story_clusters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("risk_probability", sa.Float(), nullable=False),
        sa.Column("is_risk", sa.Boolean(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("anomaly_percentile", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("model_state", sa.String(20), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint("risk_probability >= 0 AND risk_probability <= 1", name="ck_story_risk_scores_probability"),
    )
    op.create_index("ix_story_risk_scores_model", "story_risk_scores", ["model_version"])


def downgrade():
    op.drop_table("story_risk_scores")
