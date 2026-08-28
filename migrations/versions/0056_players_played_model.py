"""record on each seat WHICH MODEL it actually played

The match export reported the model by reading the agent's CURRENT
`preferred_model` every time it was generated. That is a live setting shown next
to historical data, not a record: changing an agent's model today silently
rewrote what every past match's export claimed that agent played. Comparing two
matches on different models is the point of running the same roster twice, so
the answer has to be frozen into the match.

`played_model` is stamped when a connection first claims the seat, beside the
existing `played_provider`, and the export reads the stamp.

Nullable and backfills nothing. Rows from before this column existed genuinely
do not carry the answer, and inventing one from today's settings would be the
same lie in a new place — an old export would look authoritative while being a
guess. A NULL says "not recorded", which is true.

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("players", sa.Column("played_model", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("played_model")
