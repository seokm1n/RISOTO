"""Add raw news storage and article filtering audit results.

Revision ID: 20260813_0006
Revises: 20260810_0005
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0006"
down_revision: Union[str, Sequence[str], None] = "20260810_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """원문 기사 저장과 중복·광고·관련성 필터 판정 구조를 추가한다."""
    op.create_table(
        "raw_news_articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "normalized_url",
            "content_hash",
            name="uq_raw_news_articles_source_url_content",
        ),
    )
    op.create_index(
        "ix_raw_news_articles_normalized_url",
        "raw_news_articles",
        ["normalized_url"],
    )
    op.create_index(
        "ix_raw_news_articles_content_hash",
        "raw_news_articles",
        ["content_hash"],
    )
    op.create_index(
        "ix_raw_news_articles_published_at",
        "raw_news_articles",
        ["published_at"],
    )
    op.create_index(
        "ix_raw_news_articles_collected_at",
        "raw_news_articles",
        ["collected_at"],
    )

    op.add_column(
        "news_articles",
        sa.Column("raw_article_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_news_articles_raw_article_id",
        "news_articles",
        "raw_news_articles",
        ["raw_article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_news_articles_raw_article_id",
        "news_articles",
        ["raw_article_id"],
    )

    op.create_table(
        "article_filter_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("raw_article_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("duplicate_of_raw_id", sa.BigInteger(), nullable=True),
        sa.Column("curated_article_id", sa.BigInteger(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("advertising_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("classifier_kind", sa.String(length=30), nullable=False),
        sa.Column("filter_version", sa.String(length=80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "filtered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected', 'review_required')",
            name="ck_article_filter_results_decision",
        ),
        sa.CheckConstraint(
            "reason IN ('duplicate', 'advertisement', 'irrelevant', 'accepted')",
            name="ck_article_filter_results_reason",
        ),
        sa.CheckConstraint(
            "(decision = 'accepted' AND reason IN ('accepted', 'duplicate')) OR "
            "(decision <> 'accepted' AND reason <> 'accepted')",
            name="ck_article_filter_results_decision_reason",
        ),
        sa.CheckConstraint(
            "duplicate_of_raw_id IS NULL OR duplicate_of_raw_id <> raw_article_id",
            name="ck_article_filter_results_duplicate_not_self",
        ),
        sa.CheckConstraint(
            "(reason = 'duplicate' AND duplicate_of_raw_id IS NOT NULL) OR "
            "(reason <> 'duplicate' AND duplicate_of_raw_id IS NULL)",
            name="ck_article_filter_results_duplicate_reason",
        ),
        sa.CheckConstraint(
            "relevance_score IS NULL OR "
            "(relevance_score >= 0 AND relevance_score <= 1)",
            name="ck_article_filter_results_relevance_score",
        ),
        sa.CheckConstraint(
            "advertising_score IS NULL OR "
            "(advertising_score >= 0 AND advertising_score <= 1)",
            name="ck_article_filter_results_advertising_score",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_article_filter_results_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["curated_article_id"],
            ["news_articles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_raw_id"],
            ["raw_news_articles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["raw_article_id"],
            ["raw_news_articles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "raw_article_id",
            "company_id",
            "filter_version",
            name="uq_article_filter_results_raw_company_version",
        ),
    )
    op.create_index(
        "ix_article_filter_results_company_decision_filtered",
        "article_filter_results",
        ["company_id", "decision", "filtered_at"],
    )
    op.create_index(
        "ix_article_filter_results_duplicate_of_raw_id",
        "article_filter_results",
        ["duplicate_of_raw_id"],
    )

    # Preserve all existing curated rows and their IDs. They are imported as
    # accepted legacy decisions; a separate reprocessing job can later apply
    # the current filter without making this migration network-dependent.
    op.execute(
        """
        INSERT INTO raw_news_articles (
            source, title, summary, url, original_url, normalized_url,
            content_hash, published_at, raw_payload, collected_at
        )
        SELECT
            source, title, summary, url, original_url, url,
            'legacy:' || md5(coalesce(title, '') || E'\\n' || coalesce(summary, '')),
            published_at, coalesce(raw_payload, '{}'::json), created_at
        FROM news_articles
        """
    )
    op.execute(
        """
        UPDATE news_articles AS article
        SET raw_article_id = raw.id
        FROM raw_news_articles AS raw
        WHERE raw.source = article.source
          AND raw.normalized_url = article.url
          AND raw.content_hash =
              'legacy:' || md5(coalesce(article.title, '') || E'\\n' || coalesce(article.summary, ''))
        """
    )
    op.execute(
        """
        INSERT INTO article_filter_results (
            raw_article_id, company_id, decision, reason,
            relevance_score, advertising_score, confidence,
            classifier_kind, filter_version, details, curated_article_id,
            filtered_at
        )
        SELECT
            article.raw_article_id, match.company_id, 'accepted', 'accepted',
            1.0, 0.0, 1.0,
            'legacy_import', 'legacy-import-v1',
            json_build_object(
                'legacy_import', true,
                'message', 'Filtering predates this record; preserved as accepted.'
            ),
            article.id,
            coalesce(article.created_at, CURRENT_TIMESTAMP)
        FROM news_articles AS article
        JOIN company_article_matches AS match ON match.article_id = article.id
        WHERE article.raw_article_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """기사 필터링 마이그레이션에서 추가한 구조를 제거한다."""
    op.drop_index(
        "ix_article_filter_results_duplicate_of_raw_id",
        table_name="article_filter_results",
    )
    op.drop_index(
        "ix_article_filter_results_company_decision_filtered",
        table_name="article_filter_results",
    )
    op.drop_table("article_filter_results")

    op.drop_constraint(
        "uq_news_articles_raw_article_id",
        "news_articles",
        type_="unique",
    )
    op.drop_constraint(
        "fk_news_articles_raw_article_id",
        "news_articles",
        type_="foreignkey",
    )
    op.drop_column("news_articles", "raw_article_id")

    op.drop_index(
        "ix_raw_news_articles_collected_at",
        table_name="raw_news_articles",
    )
    op.drop_index(
        "ix_raw_news_articles_published_at",
        table_name="raw_news_articles",
    )
    op.drop_index(
        "ix_raw_news_articles_content_hash",
        table_name="raw_news_articles",
    )
    op.drop_index(
        "ix_raw_news_articles_normalized_url",
        table_name="raw_news_articles",
    )
    op.drop_table("raw_news_articles")
