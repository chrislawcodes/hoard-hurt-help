"""Add connections.last_polled_at — the play-loop heartbeat.

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-15

``last_seen_at`` is bumped by ANY authenticated call, including a one-time sign-in
handshake — so a freshly-signed-in connection looks "live" without an AI actually
playing. ``last_polled_at`` only advances when the AI polls ``get_next_turn`` (the
play loop), giving an honest "is this agent running" signal used to gate seating.

Nullable with no backfill: NULL means "we've never seen this loop poll", which is
the correct default for every existing connection until its AI next polls.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_column("last_polled_at")
