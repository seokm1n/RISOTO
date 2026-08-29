"""Activate monitoring without a preparation period.

Revision ID: 20260826_0018
Revises: 20260825_0017
Create Date: 2026-08-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_0018"
down_revision: Union[str, Sequence[str], None] = "20260825_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Release companies left in the legacy backfill or warm-up states."""
    op.execute(
        sa.text(
            """
            UPDATE companies
            SET monitoring_status = 'active'
            WHERE monitoring_status IN ('backfilling', 'warming')
            """
        )
    )


def downgrade() -> None:
    """The previous preparation state cannot be reconstructed safely."""
    pass
