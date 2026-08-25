"""Record which search-context kind produced each article hit.

Revision ID: 20260820_0008
Revises: 20260820_0007
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0008"
down_revision: Union[str, Sequence[str], None] = "20260820_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "article_query_hits",
        sa.Column(
            "query_kind",
            sa.String(length=20),
            server_default="company",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_article_query_hits_kind",
        "article_query_hits",
        "query_kind IN ('company', 'alias', 'product', 'risk')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_article_query_hits_kind",
        "article_query_hits",
        type_="check",
    )
    op.drop_column("article_query_hits", "query_kind")
