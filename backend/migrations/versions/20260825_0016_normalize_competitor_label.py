"""Enforce 경쟁사 naming and normalize its PostgreSQL constraint name.

Revision ID: 20260825_0016
Revises: 20260825_0015
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0016"
down_revision: Union[str, Sequence[str], None] = "20260825_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "workspaces",
        "competitor_company_label",
        existing_type=sa.String(length=30),
        existing_nullable=False,
        server_default="경쟁사",
    )
    op.execute("UPDATE workspaces SET competitor_company_label = '경쟁사'")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'workspaces_peer_company_label_not_null'
                  AND conrelid = 'workspaces'::regclass
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'workspaces_competitor_company_label_not_null'
                  AND conrelid = 'workspaces'::regclass
            ) THEN
                ALTER TABLE workspaces
                RENAME CONSTRAINT workspaces_peer_company_label_not_null
                TO workspaces_competitor_company_label_not_null;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'workspaces_competitor_company_label_not_null'
                  AND conrelid = 'workspaces'::regclass
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'workspaces_peer_company_label_not_null'
                  AND conrelid = 'workspaces'::regclass
            ) THEN
                ALTER TABLE workspaces
                RENAME CONSTRAINT workspaces_competitor_company_label_not_null
                TO workspaces_peer_company_label_not_null;
            END IF;
        END $$;
        """
    )
