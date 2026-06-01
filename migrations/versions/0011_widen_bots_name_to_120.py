"""widen bots.name from 64 to 120 characters

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-31

DATA-SAFE on upgrade: widening a VARCHAR never loses data. SQLite can't ALTER a
column type in place, so the change goes through Alembic batch mode (copy the
table, move it back) per tests/test_migrations.py. On Postgres this is a plain
``ALTER COLUMN name TYPE VARCHAR(120)``.

Downgrade narrows back to 64. That is only safe if no stored name is longer than
64 characters; Postgres will reject the change otherwise. We don't pre-truncate
— losing a user's bot name on a rollback would be worse than a loud failure.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bots", schema=None) as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=64),
            type_=sa.String(length=120),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("bots", schema=None) as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=120),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
