"""add runner_pid to bots

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-05

Adds ``runner_pid`` (nullable integer) to ``bots``. The agent runner reports
its OS process ID at startup so the operator can kill a stuck process from the
UI. Additive only; downgrade drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bots") as batch_op:
        batch_op.add_column(sa.Column("runner_pid", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bots") as batch_op:
        batch_op.drop_column("runner_pid")
