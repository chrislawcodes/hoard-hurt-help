"""add generic per-title game state storage

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-14

Adds the storage game #2 (Liar's Dice) needs and PD does not:
- `match_state`  — public, module-owned JSON state per match.
- `player_state` — private, module-owned JSON state per (match, player).
- `turn_submissions.quantity` / `.face` — bid fields for non-PD move shapes.

All additive and nullable / empty by default. PD writes none of it, so this
migration is behavior-neutral for the existing game.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "turn_submissions", sa.Column("quantity", sa.Integer(), nullable=True)
    )
    op.add_column("turn_submissions", sa.Column("face", sa.Integer(), nullable=True))

    op.create_table(
        "match_state",
        sa.Column("match_id", sa.String(length=32), sa.ForeignKey("matches.id"), primary_key=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "player_state",
        sa.Column("match_id", sa.String(length=32), sa.ForeignKey("matches.id"), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), primary_key=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("player_state")
    op.drop_table("match_state")
    op.drop_column("turn_submissions", "face")
    op.drop_column("turn_submissions", "quantity")
