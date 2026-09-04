"""Persist per-user risk notification read state.

Revision ID: 20260904_0024
Revises: 20260902_0023
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_0024"
down_revision: Union[str, Sequence[str], None] = "20260902_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "risk_notification_reads",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("risk_event_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["risk_event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "risk_event_id"),
    )


def downgrade() -> None:
    op.drop_table("risk_notification_reads")
