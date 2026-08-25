"""Allow the complete normalized account email in response review audits.

Revision ID: 20260825_0013
Revises: 20260825_0012
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0013"
down_revision: Union[str, Sequence[str], None] = "20260825_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "response_drafts",
        "reviewed_by",
        existing_type=sa.String(length=100),
        type_=sa.String(length=320),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "response_drafts",
        "reviewed_by",
        existing_type=sa.String(length=320),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
