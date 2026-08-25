"""Add news collection jobs, articles, and company matches.

Revision ID: 20260806_0003
Revises: 20260806_0002
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0003"
down_revision: Union[str, Sequence[str], None] = "20260806_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """뉴스 수집 작업과 기업-기사 연결을 위한 데이터베이스 구조를 추가한다."""
    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'partial', 'failed')",
            name="ck_collection_jobs_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_collection_jobs_company_id", "collection_jobs", ["company_id"])

    op.create_table(
        "news_articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("ix_news_articles_published_at", "news_articles", ["published_at"])
    op.create_index("ix_news_articles_source", "news_articles", ["source"])

    op.create_table(
        "company_article_matches",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("matched_keyword", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["collection_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("company_id", "article_id"),
    )
    op.create_index("ix_company_article_matches_article_id", "company_article_matches", ["article_id"])
    op.create_index("ix_company_article_matches_job_id", "company_article_matches", ["job_id"])


def downgrade() -> None:
    """뉴스 수집 작업과 기업-기사 연결 구조를 제거한다."""
    op.drop_index("ix_company_article_matches_job_id", table_name="company_article_matches")
    op.drop_index("ix_company_article_matches_article_id", table_name="company_article_matches")
    op.drop_table("company_article_matches")
    op.drop_index("ix_news_articles_source", table_name="news_articles")
    op.drop_index("ix_news_articles_published_at", table_name="news_articles")
    op.drop_table("news_articles")
    op.drop_index("ix_collection_jobs_company_id", table_name="collection_jobs")
    op.drop_table("collection_jobs")
