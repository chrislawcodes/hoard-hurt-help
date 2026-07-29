"""add matches.mutual_help_decay — the per-match decay switch

Feature decay-switch: a per-match boolean that turns the mutual-help decay rule
ON or OFF. ON (the default) keeps today's sliding +8/+7/…→+2 per-pair decay; OFF
pays a flat +8 to each side on every mutual help. Non-null with a server default
of 1 (ON) so every existing row keeps today's decaying behavior.

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain ADD COLUMN is supported by SQLite; the server_default backfills every
    # existing row to ON so the NOT NULL holds without a separate backfill pass.
    op.add_column(
        "matches",
        sa.Column(
            "mutual_help_decay",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    # SQLite cannot DROP COLUMN in place on older engines — go through batch mode
    # (copy-and-move), the repo convention for every constraint/column drop.
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_column("mutual_help_decay")
