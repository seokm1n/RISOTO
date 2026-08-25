"""Add authentication, workspace tenancy, company roles, and financial metadata.

Revision ID: 20260825_0012
Revises: 20260820_0011
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0012"
down_revision: Union[str, Sequence[str], None] = "20260820_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# FY2025 completed-year revenue, rounded to the product's 0.01억원 precision.
# Each tuple is (UI name, normalized name, industry, KRW, size, reporting basis, source).
SEEDED_PEERS = (
    ("올리브영", "올리브영", "유통", 5_853_869_000_000, "large", "consolidated", "DART 20260327000349"),
    ("무신사", "무신사", "유통", 1_467_883_000_000, "mid_sized", "consolidated", "DART 20260331003116"),
    ("에이블리", "에이블리", "유통", 369_709_000_000, "mid_sized", "separate", "DART 20260410000090; NICE 2026-06 size"),
    ("마켓컬리", "마켓컬리", "유통", 2_367_115_000_000, "mid_sized", "consolidated", "DART 20260327000997"),
    ("SSG", "ssg", "유통", 1_347_073_000_000, "large", "consolidated", "DART 20260331002367"),
    ("11번가", "11번가", "유통", 437_623_000_000, "large", "separate", "DART 20260408003300"),
    ("카카오", "카카오", "IT·플랫폼", 8_099_148_000_000, "large", "consolidated", "Kakao FY2025 IR"),
    ("네이버", "네이버", "IT·플랫폼", 12_035_007_000_000, "large", "consolidated", "NAVER FY2025 IR"),
    ("쿠팡", "쿠팡", "유통", 41_898_416_000_000, "large", "separate", "Coupang Corp. FY2025 DART"),
)

TEST_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$4y10maHj8+Ggmj79yZ3Rbw$"
    "P//UAEBBu0x+vLfufu9h9T76UnUfQq5dWqqTT/zQ1Y8"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("peer_company_label", sa.String(length=30), server_default="동종기업", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "char_length(btrim(peer_company_label)) BETWEEN 1 AND 30",
            name="ck_workspaces_peer_company_label",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="member", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role = 'member'", name="ck_workspace_members_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.add_column("companies", sa.Column("workspace_id", sa.BigInteger(), nullable=True))
    op.add_column("companies", sa.Column("company_role", sa.String(length=20), nullable=True))
    op.add_column("companies", sa.Column("annual_revenue_krw", sa.BigInteger(), nullable=True))
    op.add_column("companies", sa.Column("company_size_class", sa.String(length=20), nullable=True))

    bind = op.get_bind()
    workspace_id = bind.execute(
        sa.text(
            "INSERT INTO workspaces (name, peer_company_label) "
            "VALUES (:name, :label) RETURNING id"
        ),
        {"name": "테스트 워크스페이스", "label": "동종기업"},
    ).scalar_one()
    user_id = bind.execute(
        sa.text(
            "INSERT INTO users (email, password_hash, is_active) "
            "VALUES (:email, :password_hash, true) RETURNING id"
        ),
        {"email": "test@test.com", "password_hash": TEST_PASSWORD_HASH},
    ).scalar_one()
    bind.execute(
        sa.text(
            "INSERT INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (:workspace_id, :user_id, 'member')"
        ),
        {"workspace_id": workspace_id, "user_id": user_id},
    )

    bind.execute(
        sa.text(
            "UPDATE companies SET workspace_id=:workspace_id, company_role='peer', "
            "annual_revenue_krw=1000000, company_size_class='small_medium'"
        ),
        {"workspace_id": workspace_id},
    )
    for name, normalized_name, industry_name, revenue_krw, size_class, _basis, _source in SEEDED_PEERS:
        result = bind.execute(
            sa.text(
                "UPDATE companies SET workspace_id=:workspace_id, company_role='peer', "
                "annual_revenue_krw=:revenue, company_size_class=:size_class "
                "WHERE name=:name"
            ),
            {
                "workspace_id": workspace_id,
                "revenue": revenue_krw,
                "size_class": size_class,
                "name": name,
            },
        )
        if result.rowcount == 0:
            bind.execute(
                sa.text(
                    "INSERT INTO companies "
                    "(workspace_id, name, normalized_name, industry_id, company_role, "
                    " annual_revenue_krw, company_size_class, backfill_days, "
                    " monitoring_status, analysis_status) "
                    "SELECT :workspace_id, :name, :normalized_name, id, 'peer', :revenue, "
                    ":size_class, 7, 'backfilling', 'pending' FROM industries "
                    "WHERE name=:industry_name"
                ),
                {
                    "workspace_id": workspace_id,
                    "name": name,
                    "normalized_name": normalized_name,
                    "industry_name": industry_name,
                    "revenue": revenue_krw,
                    "size_class": size_class,
                },
            )

    op.alter_column("companies", "workspace_id", nullable=False)
    op.alter_column("companies", "company_role", nullable=False)
    op.alter_column("companies", "annual_revenue_krw", nullable=False)
    op.alter_column("companies", "company_size_class", nullable=False)
    op.create_foreign_key(
        "fk_companies_workspace_id", "companies", "workspaces", ["workspace_id"], ["id"], ondelete="CASCADE"
    )
    op.create_check_constraint(
        "ck_companies_company_role", "companies", "company_role IN ('main', 'peer')"
    )
    op.create_check_constraint(
        "ck_companies_size_class",
        "companies",
        "company_size_class IN ('small_medium', 'mid_sized', 'large')",
    )
    op.create_check_constraint(
        "ck_companies_positive_annual_revenue", "companies", "annual_revenue_krw > 0"
    )
    op.drop_constraint("uq_companies_normalized_industry", "companies", type_="unique")
    op.drop_constraint("companies_ticker_key", "companies", type_="unique")
    op.create_unique_constraint(
        "uq_companies_workspace_normalized_industry",
        "companies",
        ["workspace_id", "normalized_name", "industry_id"],
    )
    op.create_unique_constraint(
        "uq_companies_workspace_ticker", "companies", ["workspace_id", "ticker"]
    )
    op.create_index("ix_companies_workspace_id", "companies", ["workspace_id"])
    op.create_index(
        "uq_companies_one_main_per_workspace",
        "companies",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("company_role = 'main'"),
    )

    main_company_id = bind.execute(
        sa.text(
            "INSERT INTO companies "
            "(workspace_id, name, normalized_name, industry_id, company_role, "
            " annual_revenue_krw, company_size_class, backfill_days, monitoring_status, analysis_status) "
            "SELECT :workspace_id, '기업A', '기업a', id, 'main', 100000000000, "
            "'mid_sized', 7, 'paused', 'pending' FROM industries WHERE name='유통' "
            "RETURNING id"
        ),
        {"workspace_id": workspace_id},
    ).scalar_one()
    for keyword_type, value in (
        ("alias", "A사"),
        ("product", "기업A 온라인몰"),
        ("risk", "공급망"),
        ("risk", "개인정보"),
        ("risk", "서비스 장애"),
    ):
        bind.execute(
            sa.text(
                "INSERT INTO company_keywords (company_id, keyword_type, value) "
                "VALUES (:company_id, :keyword_type, :value)"
            ),
            {"company_id": main_company_id, "keyword_type": keyword_type, "value": value},
        )

    for company_name, keyword_type, value in (
        ("마켓컬리", "alias", "컬리"),
        ("올리브영", "alias", "올영"),
        ("SSG", "alias", "신세계"),
    ):
        bind.execute(
            sa.text(
                "INSERT INTO company_keywords (company_id, keyword_type, value) "
                "SELECT id, :keyword_type, :value FROM companies "
                "WHERE workspace_id=:workspace_id AND name=:company_name "
                "ON CONFLICT (company_id, keyword_type, value) DO NOTHING"
            ),
            {
                "workspace_id": workspace_id,
                "company_name": company_name,
                "keyword_type": keyword_type,
                "value": value,
            },
        )

    bind.execute(sa.text("DELETE FROM company_keywords WHERE keyword_type='peer'"))
    op.drop_constraint("ck_company_keywords_type", "company_keywords", type_="check")
    op.create_check_constraint(
        "ck_company_keywords_type",
        "company_keywords",
        "keyword_type IN ('alias', 'product', 'risk')",
    )
    op.drop_table("company_peers")

    for table_name in ("collection_jobs", "collection_attempts", "collection_incidents"):
        op.add_column(table_name, sa.Column("workspace_id", sa.BigInteger(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE collection_jobs j SET workspace_id=c.workspace_id "
            "FROM companies c WHERE c.id=j.company_id"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE collection_attempts a SET workspace_id=c.workspace_id "
            "FROM companies c WHERE c.id=a.company_id"
        )
    )
    bind.execute(
        sa.text("UPDATE collection_incidents SET workspace_id=:workspace_id"),
        {"workspace_id": workspace_id},
    )
    for table_name in ("collection_jobs", "collection_attempts", "collection_incidents"):
        op.alter_column(table_name, "workspace_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table_name}_workspace_id",
            table_name,
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(f"ix_{table_name}_workspace_id", table_name, ["workspace_id"])

    op.add_column("response_drafts", sa.Column("workspace_id", sa.BigInteger(), nullable=True))
    op.add_column("response_drafts", sa.Column("source_company_id", sa.BigInteger(), nullable=True))
    op.add_column("response_drafts", sa.Column("target_main_company_id", sa.BigInteger(), nullable=True))
    op.add_column("response_drafts", sa.Column("generation_kind", sa.String(length=30), nullable=True))
    op.add_column(
        "response_drafts",
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("response_drafts", sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE response_drafts d SET workspace_id=c.workspace_id, source_company_id=c.id, "
            "target_main_company_id=:main_company_id FROM risk_events r "
            "JOIN companies c ON c.id=r.company_id WHERE r.id=d.risk_event_id"
        ),
        {"main_company_id": main_company_id},
    )
    op.create_foreign_key(
        "fk_response_drafts_workspace_id",
        "response_drafts",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_response_drafts_source_company_id",
        "response_drafts",
        "companies",
        ["source_company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_response_drafts_target_main_company_id",
        "response_drafts",
        "companies",
        ["target_main_company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_response_drafts_reviewed_by_user_id",
        "response_drafts",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_response_drafts_generation_kind",
        "response_drafts",
        "generation_kind IS NULL OR generation_kind IN ('main_response', 'peer_impact')",
    )
    op.create_check_constraint(
        "ck_response_drafts_schema_version", "response_drafts", "schema_version >= 1"
    )
    op.create_index("ix_response_drafts_workspace_id", "response_drafts", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_response_drafts_workspace_id", table_name="response_drafts")
    op.drop_constraint("ck_response_drafts_schema_version", "response_drafts", type_="check")
    op.drop_constraint("ck_response_drafts_generation_kind", "response_drafts", type_="check")
    op.drop_constraint("fk_response_drafts_reviewed_by_user_id", "response_drafts", type_="foreignkey")
    op.drop_constraint("fk_response_drafts_target_main_company_id", "response_drafts", type_="foreignkey")
    op.drop_constraint("fk_response_drafts_source_company_id", "response_drafts", type_="foreignkey")
    op.drop_constraint("fk_response_drafts_workspace_id", "response_drafts", type_="foreignkey")
    for column_name in (
        "reviewed_by_user_id",
        "schema_version",
        "generation_kind",
        "target_main_company_id",
        "source_company_id",
        "workspace_id",
    ):
        op.drop_column("response_drafts", column_name)

    for table_name in ("collection_incidents", "collection_attempts", "collection_jobs"):
        op.drop_index(f"ix_{table_name}_workspace_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_workspace_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "workspace_id")

    op.create_table(
        "company_peers",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("peer_company_id", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("company_id <> peer_company_id", name="ck_company_peers_not_self"),
        sa.CheckConstraint("weight > 0", name="ck_company_peers_positive_weight"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["peer_company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", "peer_company_id"),
    )
    op.create_index("ix_company_peers_peer_company_id", "company_peers", ["peer_company_id"])
    op.drop_constraint("ck_company_keywords_type", "company_keywords", type_="check")
    op.create_check_constraint(
        "ck_company_keywords_type",
        "company_keywords",
        "keyword_type IN ('alias', 'peer', 'product', 'risk')",
    )

    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM companies WHERE company_role='main' AND name='기업A'"))
    op.drop_index("uq_companies_one_main_per_workspace", table_name="companies")
    op.drop_index("ix_companies_workspace_id", table_name="companies")
    op.drop_constraint("uq_companies_workspace_ticker", "companies", type_="unique")
    op.drop_constraint("uq_companies_workspace_normalized_industry", "companies", type_="unique")
    op.create_unique_constraint(
        "uq_companies_normalized_industry", "companies", ["normalized_name", "industry_id"]
    )
    op.create_unique_constraint("companies_ticker_key", "companies", ["ticker"])
    op.drop_constraint("ck_companies_positive_annual_revenue", "companies", type_="check")
    op.drop_constraint("ck_companies_size_class", "companies", type_="check")
    op.drop_constraint("ck_companies_company_role", "companies", type_="check")
    op.drop_constraint("fk_companies_workspace_id", "companies", type_="foreignkey")
    for column_name in ("company_size_class", "annual_revenue_krw", "company_role", "workspace_id"):
        op.drop_column("companies", column_name)

    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")
