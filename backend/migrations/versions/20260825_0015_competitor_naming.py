"""Rename peer-company database concepts to competitor.

Revision ID: 20260825_0015
Revises: 20260825_0014
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0015"
down_revision: Union[str, Sequence[str], None] = "20260825_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_workspaces_peer_company_label", "workspaces", type_="check"
    )
    op.alter_column(
        "workspaces",
        "peer_company_label",
        new_column_name="competitor_company_label",
        existing_type=sa.String(length=30),
        existing_nullable=False,
        server_default="경쟁사",
    )
    op.execute("UPDATE workspaces SET competitor_company_label = '경쟁사'")
    op.create_check_constraint(
        "ck_workspaces_competitor_company_label",
        "workspaces",
        "char_length(btrim(competitor_company_label)) BETWEEN 1 AND 30",
    )

    op.drop_constraint("ck_companies_company_role", "companies", type_="check")
    op.execute(
        "UPDATE companies SET company_role = 'competitor' WHERE company_role = 'peer'"
    )
    op.alter_column(
        "companies",
        "company_role",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default="competitor",
    )
    op.create_check_constraint(
        "ck_companies_company_role",
        "companies",
        "company_role IN ('main', 'competitor')",
    )

    op.drop_constraint(
        "ck_response_drafts_generation_kind", "response_drafts", type_="check"
    )
    op.execute(
        "UPDATE response_drafts SET generation_kind = 'competitor_impact' "
        "WHERE generation_kind = 'peer_impact'"
    )
    op.execute(
        "UPDATE response_drafts "
        "SET content = (content::jsonb || "
        "jsonb_build_object('generation_kind', 'competitor_impact'))::json "
        "WHERE content->>'generation_kind' = 'peer_impact'"
    )
    op.create_check_constraint(
        "ck_response_drafts_generation_kind",
        "response_drafts",
        "generation_kind IS NULL OR generation_kind IN "
        "('main_response', 'competitor_impact')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_response_drafts_generation_kind", "response_drafts", type_="check"
    )
    op.execute(
        "UPDATE response_drafts SET generation_kind = 'peer_impact' "
        "WHERE generation_kind = 'competitor_impact'"
    )
    op.execute(
        "UPDATE response_drafts "
        "SET content = (content::jsonb || "
        "jsonb_build_object('generation_kind', 'peer_impact'))::json "
        "WHERE content->>'generation_kind' = 'competitor_impact'"
    )
    op.create_check_constraint(
        "ck_response_drafts_generation_kind",
        "response_drafts",
        "generation_kind IS NULL OR generation_kind IN "
        "('main_response', 'peer_impact')",
    )

    op.drop_constraint("ck_companies_company_role", "companies", type_="check")
    op.execute(
        "UPDATE companies SET company_role = 'peer' WHERE company_role = 'competitor'"
    )
    op.alter_column(
        "companies",
        "company_role",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default=None,
    )
    op.create_check_constraint(
        "ck_companies_company_role",
        "companies",
        "company_role IN ('main', 'peer')",
    )

    op.drop_constraint(
        "ck_workspaces_competitor_company_label", "workspaces", type_="check"
    )
    op.alter_column(
        "workspaces",
        "competitor_company_label",
        new_column_name="peer_company_label",
        existing_type=sa.String(length=30),
        existing_nullable=False,
        server_default="동종기업",
    )
    op.execute("UPDATE workspaces SET peer_company_label = '동종기업'")
    op.create_check_constraint(
        "ck_workspaces_peer_company_label",
        "workspaces",
        "char_length(btrim(peer_company_label)) BETWEEN 1 AND 30",
    )
