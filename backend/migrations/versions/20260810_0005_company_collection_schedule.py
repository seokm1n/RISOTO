"""Add per-company collection schedule.

Revision ID: 20260810_0005
Revises: 20260806_0004
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0005"
down_revision: Union[str, Sequence[str], None] = "20260806_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("next_collection_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE companies SET next_collection_at = CURRENT_TIMESTAMP + INTERVAL '15 minutes'"
    )


def downgrade() -> None:
    op.drop_column("companies", "next_collection_at")
