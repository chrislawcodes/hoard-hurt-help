"""Rename the MCP connection marker.

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-16

The old marker column used the previous internal name. The product and code now
use MCP connection, so the database column follows that language too.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_connections_mode_a_user_id_live", table_name="connections")
    with op.batch_alter_table("connections") as batch_op:
        batch_op.alter_column(
            "mode_a_at",
            new_column_name="mcp_connected_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=True,
        )
    op.create_index(
        "uq_connections_mcp_user_provider_live",
        "connections",
        ["user_id", "provider"],
        unique=True,
        sqlite_where=sa.text("mcp_connected_at IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("mcp_connected_at IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_connections_mcp_user_provider_live", table_name="connections")
    with op.batch_alter_table("connections") as batch_op:
        batch_op.alter_column(
            "mcp_connected_at",
            new_column_name="mode_a_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=True,
        )
    op.create_index(
        "uq_connections_mode_a_user_id_live",
        "connections",
        ["user_id", "provider"],
        unique=True,
        sqlite_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
    )
