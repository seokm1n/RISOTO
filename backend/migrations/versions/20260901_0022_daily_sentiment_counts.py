"""Add exact daily positive and neutral article counts.

Revision ID: 20260901_0022
Revises: 20260901_0021
Create Date: 2026-09-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0022"
down_revision: Union[str, Sequence[str], None] = "20260901_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add and backfill daily sentiment-label counts from valid-window articles."""
    op.add_column(
        "company_daily_summaries",
        sa.Column(
            "positive_article_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "company_daily_summaries",
        sa.Column(
            "neutral_article_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE company_daily_summaries AS summary
            SET positive_article_count = (
                    SELECT count(DISTINCT article.id)
                    FROM company_feature_windows AS feature_window
                    JOIN company_article_matches AS company_article
                      ON company_article.company_id = feature_window.company_id
                    JOIN news_articles AS article
                      ON article.id = company_article.article_id
                     AND COALESCE(article.published_at, article.created_at) >= feature_window.window_start
                     AND COALESCE(article.published_at, article.created_at) < feature_window.window_end
                    WHERE feature_window.company_id = summary.company_id
                      AND feature_window.data_quality <> 'unavailable'
                      AND (feature_window.window_start AT TIME ZONE 'Asia/Seoul')::date = summary.summary_date
                      AND lower(COALESCE(article.sentiment_label, '')) IN ('positive', '긍정')
                ),
                neutral_article_count = (
                    SELECT count(DISTINCT article.id)
                    FROM company_feature_windows AS feature_window
                    JOIN company_article_matches AS company_article
                      ON company_article.company_id = feature_window.company_id
                    JOIN news_articles AS article
                      ON article.id = company_article.article_id
                     AND COALESCE(article.published_at, article.created_at) >= feature_window.window_start
                     AND COALESCE(article.published_at, article.created_at) < feature_window.window_end
                    WHERE feature_window.company_id = summary.company_id
                      AND feature_window.data_quality <> 'unavailable'
                      AND (feature_window.window_start AT TIME ZONE 'Asia/Seoul')::date = summary.summary_date
                      AND lower(COALESCE(article.sentiment_label, '')) IN ('neutral', '중립')
                )
            """
        )
    )
    op.alter_column("company_daily_summaries", "positive_article_count", server_default=None)
    op.alter_column("company_daily_summaries", "neutral_article_count", server_default=None)


def downgrade() -> None:
    """Remove the daily positive and neutral article count columns."""
    op.drop_column("company_daily_summaries", "neutral_article_count")
    op.drop_column("company_daily_summaries", "positive_article_count")
