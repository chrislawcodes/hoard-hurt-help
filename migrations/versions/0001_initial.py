"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("google_sub", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_google_sub", "users", ["google_sub"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "games",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("min_players", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_players", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "per_turn_deadline_seconds", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("total_rounds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("turns_per_round", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_version", sa.String(16), nullable=False, server_default="v1"),
        sa.Column("winner_player_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_games_state", "games", ["state"])
    op.create_index("ix_games_scheduled_start", "games", ["scheduled_start"])

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("agent_key_hash", sa.String(255), nullable=False),
        sa.Column("model_self_report", sa.String(200), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_round_wins", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_round_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_round_score", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], name="fk_players_game_id_games"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_players_user_id_users"),
        sa.UniqueConstraint("game_id", "agent_id", name="uq_players_game_id_agent_id"),
        sa.UniqueConstraint("game_id", "user_id", name="uq_players_game_id_user_id"),
    )
    op.create_index("ix_players_game_id", "players", ["game_id"])
    op.create_index("ix_players_user_id", "players", ["user_id"])

    # Now we can add the deferred FK from games.winner_player_id to players.id.
    with op.batch_alter_table("games") as batch_op:
        batch_op.create_foreign_key(
            "fk_games_winner_player_id_players",
            "players",
            ["winner_player_id"],
            ["id"],
        )

    op.create_table(
        "strategy_prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name="fk_strategy_prompts_player_id_players"
        ),
    )
    op.create_index("ix_strategy_prompts_player_id", "strategy_prompts", ["player_id"])

    op.create_table(
        "turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.String(32), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("turn_token", sa.String(64), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], name="fk_turns_game_id_games"),
        sa.UniqueConstraint("turn_token", name="uq_turns_turn_token"),
        sa.UniqueConstraint("game_id", "round", "turn", name="uq_turns_game_id_round_turn"),
    )
    op.create_index("ix_turns_game_id", "turns", ["game_id"])
    op.create_index("ix_turns_deadline_at", "turns", ["deadline_at"])

    op.create_table(
        "turn_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target_player_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("points_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("round_score_after", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("was_defaulted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["turns.id"], name="fk_turn_submissions_turn_id_turns"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name="fk_turn_submissions_player_id_players"
        ),
        sa.ForeignKeyConstraint(
            ["target_player_id"],
            ["players.id"],
            name="fk_turn_submissions_target_player_id_players",
        ),
        sa.UniqueConstraint(
            "turn_id", "player_id", name="uq_turn_submissions_turn_id_player_id"
        ),
    )
    op.create_index("ix_turn_submissions_turn_id", "turn_submissions", ["turn_id"])
    op.create_index("ix_turn_submissions_player_id", "turn_submissions", ["player_id"])


def downgrade() -> None:
    op.drop_table("turn_submissions")
    op.drop_table("turns")
    op.drop_table("strategy_prompts")
    with op.batch_alter_table("games") as batch_op:
        batch_op.drop_constraint("fk_games_winner_player_id_players", type_="foreignkey")
    op.drop_table("players")
    op.drop_table("games")
    op.drop_table("users")
