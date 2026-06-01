"""two-phase turns

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "turns",
        sa.Column("phase", sa.String(length=8), nullable=False, server_default="talk"),
    )
    op.add_column(
        "turns",
        sa.Column("talk_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "turn_submissions",
        sa.Column("thinking", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "turn_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("turn_id", sa.Integer(), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("thinking", sa.Text(), nullable=False, server_default=""),
        sa.Column("was_defaulted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("turn_id", "player_id", name="uq_turn_messages_turn_id_player_id"),
    )
    op.create_index("ix_turn_messages_turn_id", "turn_messages", ["turn_id"])
    op.create_index("ix_turn_messages_player_id", "turn_messages", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_turn_messages_player_id", table_name="turn_messages")
    op.drop_index("ix_turn_messages_turn_id", table_name="turn_messages")
    op.drop_table("turn_messages")
    op.drop_column("turn_submissions", "thinking")
    op.drop_column("turns", "talk_resolved_at")
    op.drop_column("turns", "phase")
