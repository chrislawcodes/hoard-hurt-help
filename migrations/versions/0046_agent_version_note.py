"""add agent_versions.note — the owner's "what did you change" label

A short (≤140 chars), optional label the owner writes when saving a strategy
edit, shown on the agent page and version timeline. Nullable, no backfill:
existing versions simply have no note.

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite, so no batch_alter_table needed
    # here (batch is only required for drop/alter, see downgrade).
    op.add_column(
        "agent_versions",
        sa.Column("note", sa.String(length=140), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.drop_column("note")
