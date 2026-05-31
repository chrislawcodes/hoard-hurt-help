"""Add provider and model to bots so owners can configure which AI they use.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-31

DATA-SAFE: additive only. Both columns are nullable with no server_default,
so existing rows stay valid (NULL = not configured). add_column on nullable
columns is SQLite-safe — no batch mode or constraint ops needed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("provider", sa.String(16), nullable=True))
    op.add_column("bots", sa.Column("model", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("bots", "model")
    op.drop_column("bots", "provider")
