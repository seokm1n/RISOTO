"""Normalize test/admin email domains to company.com.

Revision ID: 20260828_0020
Revises: 20260828_0019
Create Date: 2026-08-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_0020"
down_revision: Union[str, Sequence[str], None] = "20260828_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace only the exact legacy test/admin email suffixes."""
    op.execute(
        sa.text(
            """
            UPDATE users
            SET email = regexp_replace(email, '@(test|admin)[.]com$', '@company.com'),
                updated_at = now()
            WHERE email ~ '@(test|admin)[.]com$'
            """
        )
    )


def downgrade() -> None:
    """The original domain cannot be inferred for arbitrary company.com users."""
    pass
