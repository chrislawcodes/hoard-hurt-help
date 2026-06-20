"""Add oauth_client_id to connection table.

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-16

Stores the OAuth Dynamic Client Registration client_id on the connection row.
Written at initialize time; used as the primary lookup key on tool calls so
stateless-HTTP mode (which has no session memory) can still route each request
to the right connection for users with multiple providers connected.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.add_column(
            sa.Column("oauth_client_id", sa.String(255), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_column("oauth_client_id")
