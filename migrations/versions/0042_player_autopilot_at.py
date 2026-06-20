"""add players.autopilot_at for human-player leave -> auto-Hoard

A human who leaves a match in progress is not removed (that is ``left_at``);
their seat keeps playing on autopilot, auto-submitting Hoard each turn so the
match never waits on a departed human while the seat stays in the standings.

No enum migration is needed for the new ``AgentKind.HUMAN`` value: agents.kind
is a ``FlexibleEnumType`` (a plain ``String`` column), so it already accepts the
new value. There is also no provider CHECK constraint in the managed schema to
relax (it was only ever model-side; see migration 0027's note).

Revision ID: 0042
Revises: 0041
Create Date: 2026-06-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite, so no batch_alter_table needed
    # here (batch is only required for drop/alter, see downgrade).
    op.add_column(
        "players",
        sa.Column("autopilot_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("autopilot_at")
