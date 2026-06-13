"""Add Mode A connection marker and live-only uniqueness.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("mode_a_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_connections_mode_a_user_id_live",
        "connections",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_connections_mode_a_user_id_live", table_name="connections")
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_column("mode_a_at")
