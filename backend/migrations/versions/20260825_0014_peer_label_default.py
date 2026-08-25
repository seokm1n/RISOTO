"""Use 동종기업 as the workspace peer-company label default.

Revision ID: 20260825_0014
Revises: 20260825_0013
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0014"
down_revision: Union[str, Sequence[str], None] = "20260825_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "workspaces",
        "peer_company_label",
        existing_type=sa.String(length=30),
        existing_nullable=False,
        server_default="동종기업",
    )
    op.execute(
        "UPDATE workspaces SET peer_company_label = '동종기업' "
        "WHERE peer_company_label = '경쟁사'"
    )


def downgrade() -> None:
    op.alter_column(
        "workspaces",
        "peer_company_label",
        existing_type=sa.String(length=30),
        existing_nullable=False,
        server_default="경쟁사",
    )
