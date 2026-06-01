"""add users.given_name and users.family_name

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-31

DATA-SAFE: additive only. Two nullable columns added to `users`. No backfill —
existing rows get NULL and are filled on the user's next login (see
app/routes/auth.py: sync_google_user). add_column of nullable columns is safe on
both SQLite and Postgres (no batch mode needed).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("given_name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("family_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "family_name")
    op.drop_column("users", "given_name")
