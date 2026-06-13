"""connection/agent split.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_connections_table() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("nickname", sa.String(length=60), nullable=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("key_lookup", sa.String(length=64), nullable=False),
        sa.Column("prev_key_lookup", sa.String(length=64), nullable=True),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(length=120), nullable=True),
        sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner_pid", sa.Integer(), nullable=True),
        sa.Column(
            "max_concurrent_games",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("stall_threshold", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_connections_user_id_users"),
    )
    op.create_index("ix_connections_user_id", "connections", ["user_id"])
    # Single unique index (matches the model's unique=True, index=True) — not a
    # separate UniqueConstraint plus a redundant non-unique index.
    op.create_index("ix_connections_key_lookup", "connections", ["key_lookup"], unique=True)
    op.create_index(
        "ix_connections_prev_key_lookup",
        "connections",
        ["prev_key_lookup"],
    )


def _create_agents_table() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="ai",
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "game",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("bot_profile_id", sa.String(length=64), nullable=True),
        sa.Column("bot_profile_name", sa.String(length=120), nullable=True),
        sa.Column("bot_strategy", sa.String(length=64), nullable=True),
        sa.Column("bot_truthfulness", sa.Integer(), nullable=True),
        sa.Column("bot_trust_model", sa.String(length=64), nullable=True),
        sa.Column("bot_seed", sa.Integer(), nullable=True),
        sa.Column("bot_version", sa.String(length=32), nullable=True),
        sa.Column("bot_fixture_pack", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_agents_user_id_users"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["connections.id"], name="fk_agents_connection_id_connections"
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_agents_user_id_name"),
        sa.UniqueConstraint(
            "user_id",
            "bot_profile_id",
            name="uq_agents_user_id_bot_profile_id",
        ),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])
    op.create_index("ix_agents_connection_id", "agents", ["connection_id"])
    op.create_index("ix_agents_game", "agents", ["game"])
    op.create_index("ix_agents_bot_profile_id", "agents", ["bot_profile_id"])


def _create_agent_versions_table() -> None:
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("strategy_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_agent_versions_agent_id_agents"),
        sa.UniqueConstraint(
            "agent_id",
            "version_no",
            name="uq_agent_versions_agent_id_version_no",
        ),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])


def _create_players_table() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("agent_version_id", sa.Integer(), nullable=True),
        sa.Column("seat_name", sa.String(length=40), nullable=False),
        sa.Column("model_self_report", sa.String(length=200), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_round_wins", sa.Float(), nullable=False),
        sa.Column("total_round_score", sa.Integer(), nullable=False),
        sa.Column("current_round_score", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], name="fk_players_match_id_matches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_players_user_id_users"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_players_agent_id_agents"),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            name="fk_players_agent_version_id_agent_versions",
        ),
        sa.UniqueConstraint(
            "match_id",
            "seat_name",
            name="uq_players_match_id_seat_name",
        ),
        sa.UniqueConstraint(
            "agent_id",
            "match_id",
            name="uq_players_agent_id_match_id",
        ),
    )
    op.create_index("ix_players_match_id", "players", ["match_id"])
    op.create_index("ix_players_user_id", "players", ["user_id"])
    op.create_index("ix_players_agent_id", "players", ["agent_id"])
    op.create_index("ix_players_agent_version_id", "players", ["agent_version_id"])


def _create_old_bots_table() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_lookup", sa.String(length=64), nullable=False),
        sa.Column("prev_key_lookup", sa.String(length=64), nullable=True),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="external"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(length=120), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_concurrent_games", sa.Integer(), nullable=False),
        sa.Column("stall_threshold", sa.Integer(), nullable=False),
        sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("sim_profile_id", sa.String(length=64), nullable=True),
        sa.Column("sim_profile_name", sa.String(length=120), nullable=True),
        sa.Column("sim_strategy", sa.String(length=64), nullable=True),
        sa.Column("sim_truthfulness", sa.Integer(), nullable=True),
        sa.Column("sim_trust_model", sa.String(length=64), nullable=True),
        sa.Column("sim_seed", sa.Integer(), nullable=True),
        sa.Column("sim_version", sa.String(length=32), nullable=True),
        sa.Column("sim_fixture_pack", sa.String(length=64), nullable=True),
        sa.Column("runner_pid", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_bots_user_id_users"),
        sa.UniqueConstraint("key_lookup", name="uq_bots_key_lookup"),
        sa.UniqueConstraint("user_id", "name", name="uq_bots_user_id_name"),
        sa.UniqueConstraint(
            "user_id",
            "sim_profile_id",
            name="uq_bots_user_id_sim_profile_id",
        ),
    )
    op.create_index("ix_bots_user_id", "bots", ["user_id"])
    op.create_index("ix_bots_key_lookup", "bots", ["key_lookup"])
    op.create_index("ix_bots_prev_key_lookup", "bots", ["prev_key_lookup"])
    op.create_index("ix_bots_sim_profile_id", "bots", ["sim_profile_id"])


def _create_old_players_table() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("model_self_report", sa.String(length=200), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_round_wins", sa.Float(), nullable=False),
        sa.Column("total_round_score", sa.Integer(), nullable=False),
        sa.Column("current_round_score", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], name="fk_players_match_id_matches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_players_user_id_users"),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], name="fk_players_bot_id_bots"),
        # NOTE: these constraint names intentionally use the historical "game_id"
        # form. The pre-0018 migration chain (when players referenced `games`)
        # drops them by these exact names on the way down to base, so the
        # downgrade-recreated table must match the historical names — NOT the
        # current match_id names. (Verified by the migration round-trip test.)
        sa.UniqueConstraint("match_id", "agent_id", name="uq_players_game_id_agent_id"),
        sa.UniqueConstraint("bot_id", "match_id", name="uq_players_bot_id_game_id"),
    )
    op.create_index("ix_players_match_id", "players", ["match_id"])
    op.create_index("ix_players_user_id", "players", ["user_id"])
    op.create_index("ix_players_bot_id", "players", ["bot_id"])


def _create_old_strategy_prompts_table() -> None:
    op.create_table(
        "strategy_prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_strategy_prompts_player_id_players",
        ),
    )
    op.create_index(
        "ix_strategy_prompts_player_id",
        "strategy_prompts",
        ["player_id"],
    )


def _drop_foreign_key_constraint(table_name: str, candidate_names: tuple[str, ...]) -> None:
    """Drop the first matching FK name from a table if it exists."""
    bind = op.get_bind()
    existing_names = {
        fk.get("name")
        for fk in inspect(bind).get_foreign_keys(table_name)
        if fk.get("name")
    }
    for name in candidate_names:
        if name in existing_names:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.drop_constraint(name, type_="foreignkey")
            return
    raise ValueError(
        f"No matching foreign key constraint found on {table_name!r}; "
        f"looked for {candidate_names!r}"
    )


def upgrade() -> None:
    # matches, turn_submissions, turn_messages all have FKs into players.
    # Drop them first so we can drop and replace the players table.
    _drop_foreign_key_constraint(
        "matches",
        ("fk_games_winner_player_id_players", "fk_matches_winner_player_id_players"),
    )
    with op.batch_alter_table("turn_submissions") as batch_op:
        batch_op.drop_constraint("fk_turn_submissions_player_id_players", type_="foreignkey")
        batch_op.drop_constraint(
            "fk_turn_submissions_target_player_id_players", type_="foreignkey"
        )
    with op.batch_alter_table("turn_messages") as batch_op:
        batch_op.drop_constraint("fk_turn_messages_player_id_players", type_="foreignkey")

    op.drop_table("strategy_prompts")
    op.drop_table("players")
    op.drop_table("bots")

    _create_connections_table()
    _create_agents_table()
    _create_agent_versions_table()
    _create_players_table()

    with op.batch_alter_table("agents") as batch_op:
        batch_op.create_foreign_key(
            "fk_agents_current_version_id_agent_versions",
            "agent_versions",
            ["current_version_id"],
            ["id"],
        )

    # Reattach the dependent FKs to the new players table.
    # Nullable columns: clear stale refs so PostgreSQL's validation scan passes.
    op.execute(sa.text("UPDATE matches SET winner_player_id = NULL"))
    op.execute(sa.text("UPDATE turn_submissions SET target_player_id = NULL"))
    with op.batch_alter_table("matches") as batch_op:
        batch_op.create_foreign_key(
            "fk_games_winner_player_id_players", "players", ["winner_player_id"], ["id"]
        )
    with op.batch_alter_table("turn_submissions") as batch_op:
        batch_op.create_foreign_key(
            "fk_turn_submissions_target_player_id_players",
            "players",
            ["target_player_id"],
            ["id"],
        )
    # player_id is NOT NULL on both tables. On PostgreSQL (prod) add the FK as
    # NOT VALID to skip the integrity scan over rows that pre-date this migration.
    # SQLite (dev/test) cannot ADD CONSTRAINT / NOT VALID, so rebuild via batch
    # mode — the fresh dev/test DB has no stale rows to validate.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE turn_submissions"
                " ADD CONSTRAINT fk_turn_submissions_player_id_players"
                " FOREIGN KEY (player_id) REFERENCES players (id) NOT VALID"
            )
        )
        op.execute(
            sa.text(
                "ALTER TABLE turn_messages"
                " ADD CONSTRAINT fk_turn_messages_player_id_players"
                " FOREIGN KEY (player_id) REFERENCES players (id) NOT VALID"
            )
        )
    else:
        with op.batch_alter_table("turn_submissions") as batch_op:
            batch_op.create_foreign_key(
                "fk_turn_submissions_player_id_players", "players", ["player_id"], ["id"]
            )
        with op.batch_alter_table("turn_messages") as batch_op:
            batch_op.create_foreign_key(
                "fk_turn_messages_player_id_players", "players", ["player_id"], ["id"]
            )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_constraint(
            "fk_agents_current_version_id_agent_versions",
            type_="foreignkey",
        )

    # Drop FKs from dependent tables before dropping the new players table.
    _drop_foreign_key_constraint(
        "matches",
        ("fk_games_winner_player_id_players", "fk_matches_winner_player_id_players"),
    )
    with op.batch_alter_table("turn_submissions") as batch_op:
        batch_op.drop_constraint("fk_turn_submissions_player_id_players", type_="foreignkey")
        batch_op.drop_constraint(
            "fk_turn_submissions_target_player_id_players", type_="foreignkey"
        )
    with op.batch_alter_table("turn_messages") as batch_op:
        batch_op.drop_constraint("fk_turn_messages_player_id_players", type_="foreignkey")

    op.drop_index("ix_players_agent_version_id", table_name="players")
    op.drop_index("ix_players_agent_id", table_name="players")
    op.drop_index("ix_players_user_id", table_name="players")
    op.drop_index("ix_players_match_id", table_name="players")
    op.drop_table("players")

    op.drop_index("ix_agent_versions_agent_id", table_name="agent_versions")
    op.drop_table("agent_versions")

    op.drop_index("ix_agents_bot_profile_id", table_name="agents")
    op.drop_index("ix_agents_game", table_name="agents")
    op.drop_index("ix_agents_connection_id", table_name="agents")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_table("agents")

    op.drop_index("ix_connections_prev_key_lookup", table_name="connections")
    op.drop_index("ix_connections_key_lookup", table_name="connections")
    op.drop_index("ix_connections_user_id", table_name="connections")
    op.drop_table("connections")

    _create_old_bots_table()
    _create_old_players_table()
    _create_old_strategy_prompts_table()

    # Reattach the dependent FKs to the restored players table.
    op.execute(sa.text("UPDATE matches SET winner_player_id = NULL"))
    op.execute(sa.text("UPDATE turn_submissions SET target_player_id = NULL"))
    with op.batch_alter_table("matches") as batch_op:
        batch_op.create_foreign_key(
            "fk_games_winner_player_id_players", "players", ["winner_player_id"], ["id"]
        )
    with op.batch_alter_table("turn_submissions") as batch_op:
        batch_op.create_foreign_key(
            "fk_turn_submissions_target_player_id_players",
            "players",
            ["target_player_id"],
            ["id"],
        )
    # player_id is NOT NULL on both tables. On PostgreSQL (prod) add the FK as
    # NOT VALID to skip the integrity scan over rows that pre-date this migration.
    # SQLite (dev/test) cannot ADD CONSTRAINT / NOT VALID, so rebuild via batch
    # mode — the fresh dev/test DB has no stale rows to validate.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE turn_submissions"
                " ADD CONSTRAINT fk_turn_submissions_player_id_players"
                " FOREIGN KEY (player_id) REFERENCES players (id) NOT VALID"
            )
        )
        op.execute(
            sa.text(
                "ALTER TABLE turn_messages"
                " ADD CONSTRAINT fk_turn_messages_player_id_players"
                " FOREIGN KEY (player_id) REFERENCES players (id) NOT VALID"
            )
        )
    else:
        with op.batch_alter_table("turn_submissions") as batch_op:
            batch_op.create_foreign_key(
                "fk_turn_submissions_player_id_players", "players", ["player_id"], ["id"]
            )
        with op.batch_alter_table("turn_messages") as batch_op:
            batch_op.create_foreign_key(
                "fk_turn_messages_player_id_players", "players", ["player_id"], ["id"]
            )
