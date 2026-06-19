"""Add players.chosen_provider for pick-the-AI-at-join.

Revision ID: 0041
Revises: 0040
Create Date: 2026-06-19

At join the user picks which connected AI plays the seat. We store that choice on
the player row so turn routing can match it (only a connection of that provider
may claim the seat) and so "one AI = one game" can be enforced. Nullable for
legacy rows created before this feature.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(
            sa.Column("chosen_provider", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("chosen_provider")
