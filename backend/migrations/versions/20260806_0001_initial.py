"""Create industry, company, and peer tables.

Revision ID: 20260806_0001
Revises:
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """RISOTO의 초기 산업·기업·기사·위험 데이터베이스 구조를 생성한다."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "industries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["industries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("ticker", sa.String(length=30), nullable=True),
        sa.Column("industry_id", sa.BigInteger(), nullable=True),
        sa.Column("backfill_days", sa.Integer(), server_default="365", nullable=False),
        sa.Column(
            "monitoring_status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("backfill_days >= 0", name="ck_companies_backfill_days"),
        sa.CheckConstraint(
            "monitoring_status IN ('active', 'paused', 'archived')",
            name="ck_companies_monitoring_status",
        ),
        sa.ForeignKeyConstraint(["industry_id"], ["industries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("ticker"),
    )
    op.create_index("ix_companies_industry_id", "companies", ["industry_id"])

    op.create_table(
        "company_peers",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("peer_company_id", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("company_id <> peer_company_id", name="ck_company_peers_not_self"),
        sa.CheckConstraint("weight > 0", name="ck_company_peers_positive_weight"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["peer_company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", "peer_company_id"),
    )
    op.create_index(
        "ix_company_peers_peer_company_id", "company_peers", ["peer_company_id"]
    )


def downgrade() -> None:
    """초기 마이그레이션에서 생성한 데이터베이스 구조를 제거한다."""
    op.drop_index("ix_company_peers_peer_company_id", table_name="company_peers")
    op.drop_table("company_peers")
    op.drop_index("ix_companies_industry_id", table_name="companies")
    op.drop_table("companies")
    op.drop_table("industries")
