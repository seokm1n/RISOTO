"""Add general/admin roles and provision the initial administrator.

Revision ID: 20260828_0019
Revises: 20260826_0018
Create Date: 2026-08-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_0019"
down_revision: Union[str, Sequence[str], None] = "20260826_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Generated with argon2id. The plaintext password is intentionally not stored
# in application code or in the database.
ADMIN_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$MoohsmmjiZC93S4qKPc9pg$"
    "08fFf//e1CFqNbNTRnn2E05uvyIdkJ9LLcpQjhv7MwY"
)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), server_default="general", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('general', 'admin')",
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO users (email, password_hash, role, is_active)
            VALUES (:email, :password_hash, 'admin', true)
            ON CONFLICT (email) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                role = 'admin',
                is_active = true,
                updated_at = now()
            """
        ),
        {
            "email": "admin@company.com",
            "password_hash": ADMIN_PASSWORD_HASH,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM users WHERE email = 'admin@company.com'"))
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
