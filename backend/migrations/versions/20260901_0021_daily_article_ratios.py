"""Add exact daily risk-article and negative-article counts.

Revision ID: 20260901_0021
Revises: 20260828_0020
Create Date: 2026-09-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0021"
down_revision: Union[str, Sequence[str], None] = "20260828_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add count columns and backfill them from valid-window article cohorts."""
    op.add_column(
        "company_daily_summaries",
        sa.Column(
            "risk_article_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "company_daily_summaries",
        sa.Column(
            "negative_article_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE company_daily_summaries AS summary
            SET negative_article_count = (
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
                      AND lower(COALESCE(article.sentiment_label, '')) IN ('negative', '부정')
                ),
                risk_article_count = (
                    SELECT count(DISTINCT risk_article.article_id)
                    FROM company_feature_windows AS feature_window
                    JOIN company_article_matches AS company_article
                      ON company_article.company_id = feature_window.company_id
                    JOIN news_articles AS article
                      ON article.id = company_article.article_id
                     AND COALESCE(article.published_at, article.created_at) >= feature_window.window_start
                     AND COALESCE(article.published_at, article.created_at) < feature_window.window_end
                    JOIN risk_event_articles AS risk_article
                      ON risk_article.article_id = article.id
                    JOIN risk_events AS risk_event
                      ON risk_event.id = risk_article.risk_event_id
                     AND risk_event.company_id = summary.company_id
                     AND risk_event.status NOT IN ('dismissed', 'legacy_candidate')
                    WHERE feature_window.company_id = summary.company_id
                      AND feature_window.data_quality <> 'unavailable'
                      AND (feature_window.window_start AT TIME ZONE 'Asia/Seoul')::date = summary.summary_date
                )
            """
        )
    )
    op.alter_column("company_daily_summaries", "risk_article_count", server_default=None)
    op.alter_column("company_daily_summaries", "negative_article_count", server_default=None)


def downgrade() -> None:
    """Remove the derived daily article count columns."""
    op.drop_column("company_daily_summaries", "negative_article_count")
    op.drop_column("company_daily_summaries", "risk_article_count")
