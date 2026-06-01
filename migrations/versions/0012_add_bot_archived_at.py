"""add bots.archived_at for soft-delete

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-31

DATA-SAFE (data-critical-waves): additive only. Adds a single nullable
`archived_at` column to `bots`. No backfill — every existing bot gets NULL,
which means "live" (the only state until an owner deletes a bot that has game
history). `add_column` of a nullable column is SQLite-safe (no batch mode, no
drop_constraint), so it does not trip the known SQLite chain caveat. The test
DB builds from model metadata, not this migration.

Post-apply verification: `bots` row count unchanged; column present and NULL
for every existing row.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bots", "archived_at")
