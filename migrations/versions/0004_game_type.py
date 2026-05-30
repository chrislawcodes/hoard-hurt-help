"""add game_type to games

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-30

DATA-AFFECTING (data-critical-waves): adds a NOT NULL `games.game_type` and
backfills existing rows to "hoard-hurt-help" via a server default. Benign — a
column add + backfill, no row deletion or data rewrite (unlike 0003). Review
before prod apply. The test DB builds from model metadata, not this migration,
so tests are unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with a server_default backfills existing rows to the PD module.
    op.add_column(
        "games",
        sa.Column(
            "game_type",
            sa.String(64),
            nullable=False,
            server_default="hoard-hurt-help",
        ),
    )
    op.create_index("ix_games_game_type", "games", ["game_type"])


def downgrade() -> None:
    op.drop_index("ix_games_game_type", table_name="games")
    op.drop_column("games", "game_type")
