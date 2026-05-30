"""persistent bots — stable per-bot credential, replacing per-game keys

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-30

DATA-AFFECTING (data-critical-waves): this migration CLEARS throwaway in-flight
game data (turn_submissions, turns, strategy_prompts, players) so it can add a
NOT NULL `players.bot_id` under the fresh-start cutover (there is no valid
backfill for old per-game keys). Confirmed acceptable: there are no real bots to
preserve. Review before running on prod. The test DB is built from model
metadata (Base.metadata.create_all), not from this migration, so tests are
unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_lookup", sa.String(length=64), nullable=False),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(length=120), nullable=True),
        sa.Column("max_concurrent_games", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("stall_threshold", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("key_lookup", name="uq_bots_key_lookup"),
        sa.UniqueConstraint("user_id", "name", name="uq_bots_user_id_name"),
    )
    op.create_index("ix_bots_user_id", "bots", ["user_id"])
    op.create_index("ix_bots_key_lookup", "bots", ["key_lookup"])

    op.create_table(
        "strategy_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_strategy_profiles_user_id_name"),
    )
    op.create_index("ix_strategy_profiles_user_id", "strategy_profiles", ["user_id"])

    # Clear throwaway game data so NOT NULL bot_id can be added. Null the
    # games.winner_player_id FK first or the players delete violates it.
    op.execute("UPDATE games SET winner_player_id = NULL")
    op.execute("DELETE FROM turn_submissions")
    op.execute("DELETE FROM turns")
    op.execute("DELETE FROM strategy_prompts")
    op.execute("DELETE FROM players")

    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bot_id", sa.Integer(), nullable=False))
        batch_op.create_index("ix_players_bot_id", ["bot_id"])
        batch_op.create_foreign_key(
            "fk_players_bot_id_bots", "bots", ["bot_id"], ["id"]
        )
        batch_op.create_unique_constraint(
            "uq_players_bot_id_game_id", ["bot_id", "game_id"]
        )
        batch_op.drop_column("agent_key_hash")


def downgrade() -> None:
    # Data is not restored — the upgrade's wipe is one-way for game rows.
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("agent_key_hash", sa.String(length=255), nullable=True)
        )
        batch_op.drop_constraint("uq_players_bot_id_game_id", type_="unique")
        batch_op.drop_constraint("fk_players_bot_id_bots", type_="foreignkey")
        batch_op.drop_index("ix_players_bot_id")
        batch_op.drop_column("bot_id")

    op.drop_index("ix_strategy_profiles_user_id", table_name="strategy_profiles")
    op.drop_table("strategy_profiles")
    op.drop_index("ix_bots_key_lookup", table_name="bots")
    op.drop_index("ix_bots_user_id", table_name="bots")
    op.drop_table("bots")
