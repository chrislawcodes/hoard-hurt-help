"""Add match_kind column to matches

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-03

Adds a VARCHAR(32) match_kind column to the matches table with a server default
of 'manual' so all existing rows get the value without a data rewrite.
Valid values: manual | practice_arena | auto_scheduled.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("matches") as b:
        b.add_column(
            sa.Column(
                "match_kind",
                sa.String(32),
                nullable=False,
                server_default="manual",
            )
        )
        b.create_index("ix_matches_match_kind", ["match_kind"])


def downgrade() -> None:
    with op.batch_alter_table("matches") as b:
        b.drop_index("ix_matches_match_kind")
        b.drop_column("match_kind")
