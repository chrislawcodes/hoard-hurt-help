"""add request_incidents table

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "request_incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("query_string", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("game_id", sa.String(length=32), nullable=True),
        sa.Column("bot_id", sa.Integer(), nullable=True),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("stacktrace", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_incidents")),
        sa.UniqueConstraint("request_id", name=op.f("uq_request_incidents_request_id")),
    )
    op.create_index(
        op.f("ix_request_incidents_request_id"),
        "request_incidents",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_incidents_path"),
        "request_incidents",
        ["path"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_incidents_user_id"),
        "request_incidents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_incidents_game_id"),
        "request_incidents",
        ["game_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_incidents_bot_id"),
        "request_incidents",
        ["bot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_incidents_player_id"),
        "request_incidents",
        ["player_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_incidents_stage"),
        "request_incidents",
        ["stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_request_incidents_stage"), table_name="request_incidents")
    op.drop_index(op.f("ix_request_incidents_player_id"), table_name="request_incidents")
    op.drop_index(op.f("ix_request_incidents_bot_id"), table_name="request_incidents")
    op.drop_index(op.f("ix_request_incidents_game_id"), table_name="request_incidents")
    op.drop_index(op.f("ix_request_incidents_user_id"), table_name="request_incidents")
    op.drop_index(op.f("ix_request_incidents_path"), table_name="request_incidents")
    op.drop_index(op.f("ix_request_incidents_request_id"), table_name="request_incidents")
    op.drop_table("request_incidents")
