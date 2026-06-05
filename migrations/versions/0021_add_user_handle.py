"""add public handle to users

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-05

Adds the public display handle to ``users``: ``handle`` (the case the user
typed), ``handle_key`` (its lowercased form, carrying a unique index so
uniqueness is case-insensitive), and ``handle_changed_at`` (for the change
cooldown). All three are nullable — existing users get NULL and pick a handle
the next time they need one. Additive only; downgrade drops them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("handle", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("handle_key", sa.String(length=20), nullable=True))
    op.add_column(
        "users",
        sa.Column("handle_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_users_handle_key", "users", ["handle_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_users_handle_key", table_name="users")
    op.drop_column("users", "handle_changed_at")
    op.drop_column("users", "handle_key")
    op.drop_column("users", "handle")
