"""Replace shared workspaces with direct user ownership.

Revision ID: 20260825_0017
Revises: 20260825_0016
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0017"
down_revision: Union[str, Sequence[str], None] = "20260825_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OWNED_TABLES = (
    "companies",
    "collection_jobs",
    "collection_attempts",
    "collection_incidents",
    "response_drafts",
)


def _raise_if_shared_workspace(bind) -> None:
    ambiguous = bind.execute(
        sa.text(
            """
            SELECT referenced.workspace_id, count(DISTINCT members.user_id) AS member_count
            FROM (
                SELECT workspace_id FROM auth_sessions
                UNION
                SELECT workspace_id FROM companies
                UNION
                SELECT workspace_id FROM collection_jobs
                UNION
                SELECT workspace_id FROM collection_attempts
                UNION
                SELECT workspace_id FROM collection_incidents
                UNION
                SELECT workspace_id FROM response_drafts WHERE workspace_id IS NOT NULL
            ) referenced
            LEFT JOIN workspace_members members
              ON members.workspace_id = referenced.workspace_id
            GROUP BY referenced.workspace_id
            HAVING count(DISTINCT members.user_id) <> 1
            LIMIT 1
            """
        )
    ).first()
    if ambiguous is not None:
        raise RuntimeError(
            "공유 또는 소유자 없는 워크스페이스의 데이터를 자동 이전할 수 없습니다: "
            f"workspace_id={ambiguous.workspace_id}, member_count={ambiguous.member_count}"
        )


def _raise_if_auth_session_owner_mismatch(bind) -> None:
    mismatch = bind.execute(
        sa.text(
            """
            SELECT
                sessions.id AS session_id,
                sessions.workspace_id,
                sessions.user_id AS session_user_id,
                members.user_id AS workspace_user_id
            FROM auth_sessions sessions
            JOIN workspace_members members
              ON members.workspace_id = sessions.workspace_id
            WHERE sessions.user_id <> members.user_id
            LIMIT 1
            """
        )
    ).first()
    if mismatch is not None:
        raise RuntimeError(
            "세션 사용자와 워크스페이스 소유자가 일치하지 않아 자동 이전할 수 없습니다: "
            f"session_id={mismatch.session_id}, workspace_id={mismatch.workspace_id}, "
            f"session_user_id={mismatch.session_user_id}, "
            f"workspace_user_id={mismatch.workspace_user_id}"
        )


def _raise_if_cross_workspace_reference(bind) -> None:
    conflict = bind.execute(
        sa.text(
            """
            WITH workspace_owners AS (
                SELECT workspace_id, min(user_id) AS user_id
                FROM workspace_members
                GROUP BY workspace_id
            )
            SELECT
                conflicts.reference,
                conflicts.record_id,
                conflicts.owner_user_id,
                conflicts.referenced_user_id
            FROM (
                SELECT
                    'collection_jobs.company_id' AS reference,
                    jobs.id AS record_id,
                    job_owners.user_id AS owner_user_id,
                    company_owners.user_id AS referenced_user_id
                FROM collection_jobs jobs
                JOIN companies companies ON companies.id = jobs.company_id
                JOIN workspace_owners job_owners
                  ON job_owners.workspace_id = jobs.workspace_id
                JOIN workspace_owners company_owners
                  ON company_owners.workspace_id = companies.workspace_id
                WHERE job_owners.user_id <> company_owners.user_id

                UNION ALL

                SELECT
                    'collection_attempts.company_id',
                    attempts.id,
                    attempt_owners.user_id,
                    company_owners.user_id
                FROM collection_attempts attempts
                JOIN companies companies ON companies.id = attempts.company_id
                JOIN workspace_owners attempt_owners
                  ON attempt_owners.workspace_id = attempts.workspace_id
                JOIN workspace_owners company_owners
                  ON company_owners.workspace_id = companies.workspace_id
                WHERE attempt_owners.user_id <> company_owners.user_id

                UNION ALL

                SELECT
                    'collection_attempts.job_id',
                    attempts.id,
                    attempt_owners.user_id,
                    job_owners.user_id
                FROM collection_attempts attempts
                JOIN collection_jobs jobs ON jobs.id = attempts.job_id
                JOIN workspace_owners attempt_owners
                  ON attempt_owners.workspace_id = attempts.workspace_id
                JOIN workspace_owners job_owners
                  ON job_owners.workspace_id = jobs.workspace_id
                WHERE attempt_owners.user_id <> job_owners.user_id

                UNION ALL

                SELECT
                    'collection_incidents.affected_company_ids',
                    incidents.id,
                    incident_owners.user_id,
                    company_owners.user_id
                FROM collection_incidents incidents
                JOIN workspace_owners incident_owners
                  ON incident_owners.workspace_id = incidents.workspace_id
                CROSS JOIN LATERAL json_array_elements(
                    CASE
                        WHEN json_typeof(incidents.affected_company_ids) = 'array'
                        THEN incidents.affected_company_ids
                        ELSE '[]'::json
                    END
                ) affected(company_id_json)
                LEFT JOIN companies companies
                  ON companies.id = CASE
                      WHEN json_typeof(affected.company_id_json) = 'number'
                           AND affected.company_id_json::text ~ '^[1-9][0-9]*$'
                      THEN CASE
                          WHEN length(affected.company_id_json::text) <= 18
                          THEN affected.company_id_json::text::bigint
                          WHEN length(affected.company_id_json::text) = 19
                               AND affected.company_id_json::text <= '9223372036854775807'
                          THEN affected.company_id_json::text::bigint
                      END
                  END
                LEFT JOIN workspace_owners company_owners
                  ON company_owners.workspace_id = companies.workspace_id
                WHERE companies.id IS NOT NULL
                  AND incident_owners.user_id <> company_owners.user_id

                UNION ALL

                SELECT
                    'response_drafts.workspace_id',
                    drafts.id,
                    draft_owners.user_id,
                    risk_owners.user_id
                FROM response_drafts drafts
                JOIN risk_events events ON events.id = drafts.risk_event_id
                JOIN companies risk_companies ON risk_companies.id = events.company_id
                JOIN workspace_owners draft_owners
                  ON draft_owners.workspace_id = drafts.workspace_id
                JOIN workspace_owners risk_owners
                  ON risk_owners.workspace_id = risk_companies.workspace_id
                WHERE drafts.workspace_id IS NOT NULL
                  AND draft_owners.user_id <> risk_owners.user_id

                UNION ALL

                SELECT
                    'response_drafts.source_company_id',
                    drafts.id,
                    risk_owners.user_id,
                    source_owners.user_id
                FROM response_drafts drafts
                JOIN risk_events events ON events.id = drafts.risk_event_id
                JOIN companies risk_companies ON risk_companies.id = events.company_id
                JOIN companies source_companies
                  ON source_companies.id = drafts.source_company_id
                JOIN workspace_owners risk_owners
                  ON risk_owners.workspace_id = risk_companies.workspace_id
                JOIN workspace_owners source_owners
                  ON source_owners.workspace_id = source_companies.workspace_id
                WHERE risk_owners.user_id <> source_owners.user_id

                UNION ALL

                SELECT
                    'response_drafts.target_main_company_id',
                    drafts.id,
                    risk_owners.user_id,
                    target_owners.user_id
                FROM response_drafts drafts
                JOIN risk_events events ON events.id = drafts.risk_event_id
                JOIN companies risk_companies ON risk_companies.id = events.company_id
                JOIN companies target_companies
                  ON target_companies.id = drafts.target_main_company_id
                JOIN workspace_owners risk_owners
                  ON risk_owners.workspace_id = risk_companies.workspace_id
                JOIN workspace_owners target_owners
                  ON target_owners.workspace_id = target_companies.workspace_id
                WHERE risk_owners.user_id <> target_owners.user_id
            ) conflicts
            LIMIT 1
            """
        )
    ).first()
    if conflict is not None:
        raise RuntimeError(
            "서로 다른 사용자 소유 워크스페이스를 참조하는 데이터를 자동 이전할 수 없습니다: "
            f"reference={conflict.reference}, record_id={conflict.record_id}, "
            f"owner_user_id={conflict.owner_user_id}, "
            f"referenced_user_id={conflict.referenced_user_id}"
        )


def _raise_if_unowned_or_conflicting(bind) -> None:
    unowned = bind.execute(
        sa.text(
            """
            SELECT referenced.source, referenced.workspace_id
            FROM (
                SELECT 'auth_sessions' AS source, workspace_id FROM auth_sessions
                UNION ALL
                SELECT 'companies', workspace_id FROM companies
                UNION ALL
                SELECT 'collection_jobs', workspace_id FROM collection_jobs
                UNION ALL
                SELECT 'collection_attempts', workspace_id FROM collection_attempts
                UNION ALL
                SELECT 'collection_incidents', workspace_id FROM collection_incidents
                UNION ALL
                SELECT 'response_drafts', workspace_id
                FROM response_drafts WHERE workspace_id IS NOT NULL
            ) referenced
            LEFT JOIN workspace_owner_map owners
              ON owners.workspace_id = referenced.workspace_id
            WHERE owners.user_id IS NULL
            LIMIT 1
            """
        )
    ).first()
    if unowned is not None:
        raise RuntimeError(
            "워크스페이스 소유자를 결정할 수 없습니다: "
            f"{unowned.source}.workspace_id={unowned.workspace_id}"
        )

    missing_owner = next(
        (
            table_name
            for table_name in OWNED_TABLES
            if bind.execute(
                sa.text(f"SELECT 1 FROM {table_name} WHERE user_id IS NULL LIMIT 1")
            ).first()
            is not None
        ),
        None,
    )
    if missing_owner is not None:
        raise RuntimeError(f"{missing_owner}의 사용자 소유권을 채울 수 없습니다.")

    duplicate_name = bind.execute(
        sa.text(
            """
            SELECT user_id, normalized_name, industry_id
            FROM companies
            WHERE industry_id IS NOT NULL
            GROUP BY user_id, normalized_name, industry_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_name is not None:
        raise RuntimeError(
            "사용자 소유권 병합 후 동일 산업의 기업명이 충돌합니다: "
            f"user_id={duplicate_name.user_id}, name={duplicate_name.normalized_name}"
        )

    duplicate_ticker = bind.execute(
        sa.text(
            """
            SELECT user_id, ticker
            FROM companies
            WHERE ticker IS NOT NULL
            GROUP BY user_id, ticker
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_ticker is not None:
        raise RuntimeError(
            "사용자 소유권 병합 후 종목코드가 충돌합니다: "
            f"user_id={duplicate_ticker.user_id}, ticker={duplicate_ticker.ticker}"
        )

    duplicate_main = bind.execute(
        sa.text(
            """
            SELECT user_id
            FROM companies
            WHERE company_role = 'main'
            GROUP BY user_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_main is not None:
        raise RuntimeError(
            "한 사용자가 여러 워크스페이스의 메인 기업을 소유하게 됩니다: "
            f"user_id={duplicate_main.user_id}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _raise_if_shared_workspace(bind)
    _raise_if_auth_session_owner_mismatch(bind)
    _raise_if_cross_workspace_reference(bind)
    bind.execute(
        sa.text(
            """
            CREATE TEMPORARY TABLE workspace_owner_map
            ON COMMIT DROP
            AS
            SELECT DISTINCT ON (workspace_id)
                workspace_id,
                user_id
            FROM workspace_members
            ORDER BY workspace_id, joined_at, user_id
            """
        )
    )
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_workspace_owner_map_workspace "
            "ON workspace_owner_map (workspace_id)"
        )
    )

    for table_name in OWNED_TABLES:
        op.add_column(table_name, sa.Column("user_id", sa.BigInteger(), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE companies companies
            SET user_id = owners.user_id
            FROM workspace_owner_map owners
            WHERE owners.workspace_id = companies.workspace_id
            """
        )
    )
    for table_name in ("collection_jobs", "collection_attempts"):
        bind.execute(
            sa.text(
                f"""
                UPDATE {table_name} owned
                SET user_id = companies.user_id
                FROM companies
                WHERE companies.id = owned.company_id
                """
            )
        )
    bind.execute(
        sa.text(
            """
            UPDATE collection_incidents incidents
            SET user_id = owners.user_id
            FROM workspace_owner_map owners
            WHERE owners.workspace_id = incidents.workspace_id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE response_drafts drafts
            SET user_id = COALESCE(
                (
                    SELECT owners.user_id
                    FROM workspace_owner_map owners
                    WHERE owners.workspace_id = drafts.workspace_id
                ),
                (
                    SELECT companies.user_id
                    FROM companies
                    WHERE companies.id = drafts.source_company_id
                ),
                (
                    SELECT companies.user_id
                    FROM companies
                    WHERE companies.id = drafts.target_main_company_id
                ),
                (
                    SELECT companies.user_id
                    FROM risk_events
                    JOIN companies ON companies.id = risk_events.company_id
                    WHERE risk_events.id = drafts.risk_event_id
                )
            )
            """
        )
    )

    _raise_if_unowned_or_conflicting(bind)

    for table_name in OWNED_TABLES:
        op.alter_column(
            table_name,
            "user_id",
            existing_type=sa.BigInteger(),
            nullable=False,
        )

    op.drop_index("uq_companies_one_main_per_workspace", table_name="companies")
    op.drop_index("ix_companies_workspace_id", table_name="companies")
    op.drop_constraint(
        "uq_companies_workspace_ticker", "companies", type_="unique"
    )
    op.drop_constraint(
        "uq_companies_workspace_normalized_industry", "companies", type_="unique"
    )
    op.drop_constraint("fk_companies_workspace_id", "companies", type_="foreignkey")

    for table_name in (
        "collection_jobs",
        "collection_attempts",
        "collection_incidents",
    ):
        op.drop_index(f"ix_{table_name}_workspace_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_workspace_id", table_name, type_="foreignkey"
        )

    op.drop_index("ix_response_drafts_workspace_id", table_name="response_drafts")
    op.drop_constraint(
        "fk_response_drafts_workspace_id", "response_drafts", type_="foreignkey"
    )
    op.drop_constraint(
        "auth_sessions_workspace_id_fkey", "auth_sessions", type_="foreignkey"
    )

    op.create_foreign_key(
        "fk_companies_user_id",
        "companies",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_companies_user_normalized_industry",
        "companies",
        ["user_id", "normalized_name", "industry_id"],
    )
    op.create_unique_constraint(
        "uq_companies_user_ticker", "companies", ["user_id", "ticker"]
    )
    op.create_index("ix_companies_user_id", "companies", ["user_id"])
    op.create_index(
        "uq_companies_one_main_per_user",
        "companies",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("company_role = 'main'"),
    )

    for table_name in (
        "collection_jobs",
        "collection_attempts",
        "collection_incidents",
        "response_drafts",
    ):
        op.create_foreign_key(
            f"fk_{table_name}_user_id",
            table_name,
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(f"ix_{table_name}_user_id", table_name, ["user_id"])

    op.drop_column("auth_sessions", "workspace_id")
    for table_name in OWNED_TABLES:
        op.drop_column(table_name, "workspace_id")

    op.drop_table("workspace_members")
    op.drop_table("workspaces")


def downgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "workspaces",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "competitor_company_label",
            sa.String(length=30),
            server_default="경쟁사",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(competitor_company_label)) BETWEEN 1 AND 30",
            name="ck_workspaces_competitor_company_label",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            server_default="member",
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("role = 'member'", name="ck_workspace_members_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    bind.execute(
        sa.text(
            """
            INSERT INTO workspaces (id, name, competitor_company_label)
            OVERRIDING SYSTEM VALUE
            SELECT id, left('개인 공간 - ' || email, 120), '경쟁사'
            FROM users
            """
        )
    )
    bind.execute(
        sa.text(
            """
            SELECT setval(
                pg_get_serial_sequence('workspaces', 'id'),
                COALESCE(max(id), 1),
                max(id) IS NOT NULL
            )
            FROM workspaces
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO workspace_members (workspace_id, user_id, role)
            SELECT id, id, 'member' FROM users
            """
        )
    )

    op.add_column(
        "auth_sessions", sa.Column("workspace_id", sa.BigInteger(), nullable=True)
    )
    for table_name in OWNED_TABLES:
        op.add_column(
            table_name, sa.Column("workspace_id", sa.BigInteger(), nullable=True)
        )

    bind.execute(sa.text("UPDATE auth_sessions SET workspace_id = user_id"))
    for table_name in OWNED_TABLES:
        bind.execute(sa.text(f"UPDATE {table_name} SET workspace_id = user_id"))

    op.alter_column(
        "auth_sessions",
        "workspace_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    for table_name in OWNED_TABLES[:-1]:
        op.alter_column(
            table_name,
            "workspace_id",
            existing_type=sa.BigInteger(),
            nullable=False,
        )

    op.drop_index("uq_companies_one_main_per_user", table_name="companies")
    op.drop_index("ix_companies_user_id", table_name="companies")
    op.drop_constraint("uq_companies_user_ticker", "companies", type_="unique")
    op.drop_constraint(
        "uq_companies_user_normalized_industry", "companies", type_="unique"
    )
    op.drop_constraint("fk_companies_user_id", "companies", type_="foreignkey")

    for table_name in (
        "collection_jobs",
        "collection_attempts",
        "collection_incidents",
        "response_drafts",
    ):
        op.drop_index(f"ix_{table_name}_user_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_user_id", table_name, type_="foreignkey")

    op.create_foreign_key(
        "fk_companies_workspace_id",
        "companies",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
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

    for table_name in (
        "collection_jobs",
        "collection_attempts",
        "collection_incidents",
        "response_drafts",
    ):
        op.create_foreign_key(
            f"fk_{table_name}_workspace_id",
            table_name,
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(f"ix_{table_name}_workspace_id", table_name, ["workspace_id"])
    op.create_foreign_key(
        "auth_sessions_workspace_id_fkey",
        "auth_sessions",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )

    for table_name in OWNED_TABLES:
        op.drop_column(table_name, "user_id")
