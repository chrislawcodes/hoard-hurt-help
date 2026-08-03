"""replace matches.mutual_help_decay with matches.mutual_help_mode

The yes/no decay switch could only express two of the rules we want to compare.
This widens it to a named mode: "decay" (the sliding +8/+7/…→+2), "once" (the
bonus paid only on a pair's first mutual help), and flat_8 / flat_7 / flat_6
(a fixed per-side total every time).

The two old values map exactly onto two of the new ones — decay ON was "decay",
OFF was "flat_8" — so every finished match keeps the rule it was actually played
under. That matters: these rows are experiment results, and silently relabelling
one arm would corrupt a comparison rather than break something visibly.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable first, translate every existing row, then tighten to NOT NULL.
    # Adding it NOT NULL with a server default would stamp every row "decay",
    # including the decay-OFF matches — quietly rewriting what those games were
    # played under.
    op.add_column("matches", sa.Column("mutual_help_mode", sa.String(16), nullable=True))
    op.execute(
        "UPDATE matches SET mutual_help_mode = "
        "CASE WHEN mutual_help_decay THEN 'decay' ELSE 'flat_8' END"
    )
    with op.batch_alter_table("matches") as batch_op:
        batch_op.alter_column(
            "mutual_help_mode",
            existing_type=sa.String(16),
            nullable=False,
            server_default="decay",
        )
        # SQLite cannot DROP COLUMN in place on older engines — batch mode
        # (copy-and-move) is the repo convention for every column drop.
        batch_op.drop_column("mutual_help_decay")


def downgrade() -> None:
    # Only "decay" and "flat_8" have a boolean equivalent. Anything else is
    # collapsed to the closest rule (decay), which loses information — that is
    # unavoidable going back to a two-value column, and is why the comment says
    # so out loud rather than failing silently.
    op.add_column(
        "matches",
        sa.Column("mutual_help_decay", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.execute(
        "UPDATE matches SET mutual_help_decay = "
        "CASE WHEN mutual_help_mode = 'flat_8' THEN 0 ELSE 1 END"
    )
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_column("mutual_help_mode")
