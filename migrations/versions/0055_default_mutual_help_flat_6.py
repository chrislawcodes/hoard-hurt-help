"""point matches.mutual_help_mode's column default at flat_6

The rule a new match gets moved from "decay" to "flat_6". Every create path in
the app passes the mode explicitly, so this default is a backstop that fires
only on a raw INSERT that omits the column — but leaving it on "decay" while the
app creates flat_6 matches is exactly the drift that misleads the next reader,
and a raw insert would stamp a match with a rule nothing else still uses.

Deliberately touches no existing row. Each match stores the rule it was actually
played under, and those rows are experiment results: silently relabelling one
would corrupt a comparison rather than break something visibly. The live
Practice Arena is moved by app/engine/arena.py's staleness check, which cancels
and rebuilds it — a new match, not a rewritten one.

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_server_default(value: str) -> None:
    # Batch mode is the repo convention for altering a column: SQLite cannot
    # change a column in place, so alembic copies the table and moves it over.
    with op.batch_alter_table("matches") as batch_op:
        batch_op.alter_column(
            "mutual_help_mode",
            existing_type=sa.String(16),
            existing_nullable=False,
            server_default=value,
        )


def upgrade() -> None:
    _set_server_default("flat_6")


def downgrade() -> None:
    _set_server_default("decay")
