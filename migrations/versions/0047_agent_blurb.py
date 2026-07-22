"""add agents.blurb — the owner's short label for telling agents apart

A short (≤32 chars), optional label the owner writes for an agent ("Forgives
once"), shown beside the name on the join lineup and the agents list. Nullable,
no backfill: existing agents simply have no blurb and render as name only.

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite, so no batch_alter_table needed
    # here (batch is only required for drop/alter, see downgrade).
    op.add_column(
        "agents",
        sa.Column("blurb", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("blurb")
