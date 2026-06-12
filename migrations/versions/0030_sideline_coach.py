"""Add sideline coaching: Player.coach_note/coach_note_round + Match.coaching.

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column("coach_note", sa.String(280), nullable=True))
        batch_op.add_column(sa.Column("coach_note_round", sa.Integer(), nullable=True))

    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "coaching",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            )
        )

    # Backfill existing match rows so coaching=True (server_default only applies
    # to new INSERTs, not existing rows).
    op.execute(sa.text("UPDATE matches SET coaching = 1 WHERE coaching IS NULL OR coaching = 0"))


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("coach_note_round")
        batch_op.drop_column("coach_note")

    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_column("coaching")
