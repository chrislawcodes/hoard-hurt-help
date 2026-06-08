"""Add draft connection setups.

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connection_setups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("nickname", sa.String(length=60), nullable=True),
        sa.Column("key_lookup", sa.String(length=64), nullable=False),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_connection_setups_user_id_users"),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name="fk_connection_setups_connection_id_connections",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_connection_setups_user_id", "connection_setups", ["user_id"])
    op.create_index("ix_connection_setups_key_lookup", "connection_setups", ["key_lookup"], unique=True)
    op.create_index(
        "ix_connection_setups_connection_id",
        "connection_setups",
        ["connection_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_connection_setups_connection_id", table_name="connection_setups")
    op.drop_index("ix_connection_setups_key_lookup", table_name="connection_setups")
    op.drop_index("ix_connection_setups_user_id", table_name="connection_setups")
    op.drop_table("connection_setups")
