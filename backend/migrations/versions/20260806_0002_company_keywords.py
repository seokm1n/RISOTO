"""Add company keywords and default industries.

Revision ID: 20260806_0002
Revises: 20260806_0001
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0002"
down_revision: Union[str, Sequence[str], None] = "20260806_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_INDUSTRIES = (
    ("IT·플랫폼", "소프트웨어, 인터넷 서비스 및 디지털 플랫폼"),
    ("건설·부동산", "건설, 개발, 주택 및 상업용 부동산"),
    ("금융", "은행, 증권, 보험 및 금융 서비스"),
    ("바이오·헬스케어", "제약, 바이오, 의료기기 및 의료 서비스"),
    ("반도체·전자", "반도체, 전자부품 및 전자제품"),
    ("식음료·외식", "식품, 음료, 카페 및 외식 프랜차이즈"),
    ("에너지·화학", "에너지, 정유, 배터리 및 화학 소재"),
    ("유통", "온·오프라인 유통, 편의점 및 종합 소매"),
    ("자동차·모빌리티", "자동차, 부품 및 모빌리티 서비스"),
    ("콘텐츠·엔터테인먼트", "게임, 미디어, 음악 및 엔터테인먼트"),
    ("기타", "기존 분류에 포함되지 않는 산업"),
)


def upgrade() -> None:
    op.create_table(
        "company_keywords",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("keyword_type", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "keyword_type IN ('alias', 'peer', 'product', 'risk')",
            name="ck_company_keywords_type",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "keyword_type", "value", name="uq_company_keywords_value"
        ),
    )
    op.create_index("ix_company_keywords_company_id", "company_keywords", ["company_id"])
    op.create_index("ix_company_keywords_value", "company_keywords", ["value"])

    industries = sa.table(
        "industries",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        industries,
        [{"name": name, "description": description} for name, description in DEFAULT_INDUSTRIES],
    )


def downgrade() -> None:
    names = [name for name, _ in DEFAULT_INDUSTRIES]
    op.execute(
        sa.text("DELETE FROM industries WHERE name = ANY(:names)").bindparams(names=names)
    )
    op.drop_index("ix_company_keywords_value", table_name="company_keywords")
    op.drop_index("ix_company_keywords_company_id", table_name="company_keywords")
    op.drop_table("company_keywords")
