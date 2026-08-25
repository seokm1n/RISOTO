"""Enforce one curated article per normalized URL.

Revision ID: 20260820_0009
Revises: 20260820_0008
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260820_0009"
down_revision: Union[str, Sequence[str], None] = "20260820_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_news_articles_normalized_url",
        "news_articles",
        ["url"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_news_articles_normalized_url",
        "news_articles",
        type_="unique",
    )
