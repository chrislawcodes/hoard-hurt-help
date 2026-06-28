"""add agents.preferred_model for per-agent model selection

An agent gains an optional, mutable preferred AI model (NULL = use the provider's
default). It is honored only on a machine connection when it matches the seat's
chosen provider; MCP clients ignore it. It is deliberately on the Agent (mutable),
not the immutable AgentVersion, because the model is not part of strategy identity.

Revision ID: 0044
Revises: 0043
Create Date: 2026-06-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite, so no batch_alter_table needed
    # here (batch is only required for drop/alter, see downgrade).
    op.add_column(
        "agents",
        sa.Column("preferred_model", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("preferred_model")
