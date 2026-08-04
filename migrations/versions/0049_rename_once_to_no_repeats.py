"""rename the 'once' mutual-help mode to 'no_repeats'

The mode shipped one revision ago meaning "bonus paid only the first time a pair
ever helps mutually". The intended rule is a one-turn cooldown instead: the full
bonus every time EXCEPT when that same pair also went mutual on the previous turn.
Different rule, so it gets a name that describes it.

'once' only existed between 0048 and this revision, so in practice no match was
played under it — but renaming the stored value anyway means a row that did slip
through keeps working instead of raising on an unknown mode at resolve time.

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE matches SET mutual_help_mode = 'no_repeats' WHERE mutual_help_mode = 'once'")


def downgrade() -> None:
    op.execute("UPDATE matches SET mutual_help_mode = 'once' WHERE mutual_help_mode = 'no_repeats'")
