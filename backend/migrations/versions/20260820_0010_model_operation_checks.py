"""Add persisted daily model-operation checks and remove a redundant URL constraint.

Revision ID: 20260820_0010
Revises: 20260820_0009
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0010"
down_revision: Union[str, Sequence[str], None] = "20260820_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0003 created an unnamed URL unique constraint. 0009 replaced it with a
    # stable explicit name, so keeping both would only duplicate the index.
    op.drop_constraint("news_articles_url_key", "news_articles", type_="unique")
    op.create_table(
        "model_operation_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("check_date", sa.Date(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('stable', 'warning', 'insufficient_data')",
            name="ck_model_operation_checks_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_date", name="uq_model_operation_checks_date"),
    )
    op.create_index(
        "ix_model_operation_checks_checked_at",
        "model_operation_checks",
        ["checked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_operation_checks_checked_at",
        table_name="model_operation_checks",
    )
    op.drop_table("model_operation_checks")
    op.create_unique_constraint(
        "news_articles_url_key",
        "news_articles",
        ["url"],
    )
