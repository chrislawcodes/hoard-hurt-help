"""add bots.first_connected_at for the onboarding handshake

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-30

DATA-SAFE (data-critical-waves): additive only. Adds a single nullable
`first_connected_at` column to `bots`. No backfill — existing bots get NULL,
which the onboarding resolver treats correctly via play-history precedence
(a bot that has already moved shows the "playing" state regardless of NULL).
`add_column` of a nullable column is SQLite-safe (no batch mode, no
drop_constraint), so it does not trip the known SQLite chain caveat. The test
DB builds from model metadata, not this migration.

Post-apply verification: `bots` row count unchanged; column present.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bots", "first_connected_at")
