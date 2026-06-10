"""Drop agents.connection_id column.

Agents now route by the stored provider column. The connection_id column was
retained through migration 0026 to backfill agents.provider; it is no longer
read or written anywhere, so we drop it here.

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE on the bot/connection CHECK: the model carried a CheckConstraint
    # ``ck_agents_bot_connection_null`` ("kind != 'bot' OR connection_id IS NULL"),
    # but NO migration ever added it to the managed schema — production (Postgres,
    # built from migrations) has no such constraint, and neither does the
    # migration-built SQLite schema. So this migration must NOT try to drop it:
    # doing so would crash the prod deploy with "constraint does not exist". We
    # drop only the index, the FK, and the column. (The model removes the
    # constraint definition in the same change, keeping model and schema aligned.)
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_index("ix_agents_connection_id")
        batch_op.drop_constraint(
            "fk_agents_connection_id_connections", type_="foreignkey"
        )
        batch_op.drop_column("connection_id")


def downgrade() -> None:
    # Restore exactly what the managed schema had before: the column, FK, and
    # index — but NOT the model-only CHECK (it was never in the schema).
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("connection_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_agents_connection_id_connections",
            "connections",
            ["connection_id"],
            ["id"],
        )
        batch_op.create_index("ix_agents_connection_id", ["connection_id"])
