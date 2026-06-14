"""add players.seat_reserved_until for join-before-connect seat holds

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite, so no batch_alter_table needed
    # here (batch is only required for drop/alter, see downgrade).
    op.add_column(
        "players",
        sa.Column("seat_reserved_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("seat_reserved_until")
