"""Add per-connection usage counters: api_call_count + turns_played.

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-13

Adds two lifetime counters to ``connections`` so the connection detail page can
show how much a connection has been used — every authenticated agent call
(``api_call_count``, each one a paid model call in interactive MCP mode) and
every real move submitted (``turns_played``). Both default to 0 for existing
rows. Additive only; downgrade drops the columns.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "api_call_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "turns_played",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_column("turns_played")
        batch_op.drop_column("api_call_count")
