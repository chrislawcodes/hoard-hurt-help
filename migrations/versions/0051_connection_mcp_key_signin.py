"""add connections.mcp_key_signin_enabled — opt-in key auth on /mcp

Off for every existing connection. /mcp stays Google-sign-in-only unless the
owner deliberately turns this on for one connection, which they only need to do
for a client that cannot finish the OAuth flow (Antigravity).

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite, so no batch_alter_table needed
    # here (batch is only required for drop/alter, see downgrade). The
    # server_default backfills every existing row to false — nobody gains key
    # sign-in by being migrated.
    op.add_column(
        "connections",
        sa.Column(
            "mcp_key_signin_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_column("mcp_key_signin_enabled")
