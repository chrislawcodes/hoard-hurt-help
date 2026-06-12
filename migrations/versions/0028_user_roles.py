"""Add user roles and match ownership.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import settings

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(length=16),
                nullable=False,
                server_default="user",
            )
        )

    conn = op.get_bind()
    for email in sorted(settings.platform_admin_emails_set):
        conn.execute(
            sa.text("UPDATE users SET role = 'admin' WHERE lower(email) = :email"),
            {"email": email},
        )

    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(
            sa.Column("created_by_user_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_matches_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_matches_created_by_user_id",
            ["created_by_user_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_index("ix_matches_created_by_user_id")
        batch_op.drop_constraint(
            "fk_matches_created_by_user_id_users",
            type_="foreignkey",
        )
        batch_op.drop_column("created_by_user_id")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("role")
