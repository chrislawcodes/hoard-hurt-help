"""bot heartbeat (last_seen_at) + graceful-reissue previous key

Adds two columns to ``bots``:
  * ``last_seen_at`` — live heartbeat, stamped on every authenticated agent call,
    so the health badge can tell "runner alive now" from "connected once".
  * ``prev_key_lookup`` — the previous key's sha256 during a graceful reissue,
    so reissuing doesn't knock a still-running bot offline.

Adds-only; safe on SQLite without batch mode.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bots",
        sa.Column("prev_key_lookup", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_bots_prev_key_lookup", "bots", ["prev_key_lookup"])


def downgrade() -> None:
    op.drop_index("ix_bots_prev_key_lookup", table_name="bots")
    op.drop_column("bots", "prev_key_lookup")
    op.drop_column("bots", "last_seen_at")
