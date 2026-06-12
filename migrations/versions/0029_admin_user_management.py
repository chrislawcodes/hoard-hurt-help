"""Add admin user management: users.disabled_at + admin_audit_log table.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "disabled_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_admin_audit_log_actor_user_id",
        "admin_audit_log",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_admin_audit_log_target_user_id",
        "admin_audit_log",
        ["target_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_audit_log_target_user_id", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_actor_user_id", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("disabled_at")
