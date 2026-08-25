"""Normalize existing workspace labels to 경쟁사.

Revision ID: 20260825_0016
Revises: 20260825_0015
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260825_0016"
down_revision: Union[str, Sequence[str], None] = "20260825_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE workspaces SET competitor_company_label = '경쟁사'")


def downgrade() -> None:
    op.execute("UPDATE workspaces SET competitor_company_label = '동종기업'")
