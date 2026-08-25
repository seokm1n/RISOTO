"""Add explicit model artifact dependency metadata.

Revision ID: 20260820_0011
Revises: 20260820_0010
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0011"
down_revision: Union[str, Sequence[str], None] = "20260820_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_versions",
        sa.Column(
            "dependencies",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.alter_column("model_versions", "dependencies", server_default=None)


def downgrade() -> None:
    op.drop_column("model_versions", "dependencies")
