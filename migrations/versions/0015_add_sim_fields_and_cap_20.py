"""add sim fields to bots and cap hoard-hurt-help at 20 players

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-03

Adds the Sim trait columns to ``bots`` and changes the Hoard-Hurt-Help game
default cap to 20 players. The migration is additive except for the
``games.max_players`` default, which goes through Alembic batch mode so SQLite
tests can round-trip cleanly.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="external",
        ),
    )
    op.add_column("bots", sa.Column("sim_strategy", sa.String(length=64), nullable=True))
    op.add_column(
        "bots", sa.Column("sim_truthfulness", sa.Integer(), nullable=True)
    )
    op.add_column(
        "bots", sa.Column("sim_trust_model", sa.String(length=64), nullable=True)
    )
    op.add_column("bots", sa.Column("sim_seed", sa.Integer(), nullable=True))
    op.add_column("bots", sa.Column("sim_version", sa.String(length=32), nullable=True))
    op.add_column(
        "bots", sa.Column("sim_fixture_pack", sa.String(length=64), nullable=True)
    )

    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.alter_column(
            "max_players",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default="20",
        )


def downgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.alter_column(
            "max_players",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default="100",
        )

    op.drop_column("bots", "sim_fixture_pack")
    op.drop_column("bots", "sim_version")
    op.drop_column("bots", "sim_seed")
    op.drop_column("bots", "sim_trust_model")
    op.drop_column("bots", "sim_truthfulness")
    op.drop_column("bots", "sim_strategy")
    op.drop_column("bots", "kind")

