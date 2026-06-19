"""Decouple agents from a fixed AI model/provider.

Revision ID: 0040
Revises: 0039
Create Date: 2026-06-18

Agents are now just name + strategy — the user no longer picks a model, and
turns route to any of the user's live connections (not a provider-matched one).
Two schema changes support that:

- ``agent_versions.model`` becomes nullable. New versions leave it NULL; the
  column is kept for historical rows.
- ``players.played_provider`` is added. It records the provider that actually
  played a seat (stamped from the connection that first claims a turn), so the
  public "played by Claude/Gemini/…" badge has a deletion-proof source of truth.

SQLite can't ALTER a column in place, so both go through Alembic batch mode.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=64),
            nullable=True,
        )
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(
            sa.Column("played_provider", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("played_provider")
    # Restore NOT NULL. Existing NULLs would block this, so backfill a sentinel
    # first — only matters when downgrading a DB that already has model-less
    # versions, which is the post-decouple state.
    op.execute("UPDATE agent_versions SET model = '' WHERE model IS NULL")
    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=64),
            nullable=False,
        )
