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
    op.drop_constraint("uq_players_game_id_user_id", "players", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_players_game_id_user_id", "players", ["game_id", "user_id"]
    )
