"""allow multiple agents per user per game

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ALTER a constraint in place; batch mode rebuilds the table
    # via copy-and-move. Harmless no-op pass-through on Postgres.
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.drop_constraint("uq_players_game_id_user_id", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_players_game_id_user_id", ["game_id", "user_id"]
        )
