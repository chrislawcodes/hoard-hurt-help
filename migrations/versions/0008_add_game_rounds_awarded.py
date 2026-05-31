"""add games.rounds_awarded to make round awarding idempotent on resume

Revision ID: 0008
Revises: 0006
Create Date: 2026-05-31

DATA-SAFE (data-critical-waves): additive only. Adds a single
`rounds_awarded` integer column to `games`, server_default "0" so existing
rows backfill to "no rounds awarded yet" — which is the correct value for any
in-flight game (award_round only ever increases it). `add_column` with a
server_default is SQLite-safe (no batch mode, no drop_constraint), so it does
not trip the known SQLite constraint-op caveat. The test DB builds from model
metadata, not this migration.

Chained from 0006 (the head on main). 0007 is intentionally skipped here.

Post-apply verification: `games` row count unchanged; column present and 0 for
all existing rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("rounds_awarded", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("games", "rounds_awarded")
