"""rename game → match (feature 009)

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-03

DATA-AFFECTING (data-critical-waves). Two changes in one atomic migration:

1. SCHEMA: table ``games`` → ``matches``; column ``games.game_type`` → ``game``;
   the match foreign keys ``players.game_id`` / ``turns.game_id`` and the tracing
   column ``request_incidents.game_id`` → ``match_id``; the indexes on those
   columns follow.
2. DATA: every match id is rewritten from the legacy ``G_`` prefix to ``M_``
   (PK on matches, plus every referencing column). The swap is a pure prefix
   replace; ``app.engine.match_id_rewrite`` is the shared contract with
   ``scripts/preview_match_id_migration.py`` so the dry-run plan can never drift.
   Real prod ids are not all ``G_NNNN`` (there is at least one ``G_demo``); the
   prefix swap covers any ``G_`` id.

CONSTRAINT NAMES: this migration renames columns and indexes but does NOT rewrite
the internal *names* of the UNIQUE / FOREIGN KEY constraints (they keep their
``..._game_id_...`` / ``fk_..._games`` spelling). SQLite cannot reliably reflect a
batch-created named UNIQUE constraint back out for a later drop, so renaming them
here is not round-trip-safe. The names are cosmetic — they do not affect
behaviour — and the whole-suite test DB is built from model metadata (with the
correct ``match`` names), not from this chain. Only tests/test_migrations.py and
prod exercise this migration. ``alter_column`` carries each constraint onto the
renamed column automatically, so the constraints still guard the right columns.

FK enforcement is OFF during Alembic SQLite migrations (env.py sets no
``PRAGMA foreign_keys``), so the value rewrite needs no drop/re-add dance. All
schema changes go through ``op.batch_alter_table`` (SQLite cannot ALTER in
place — MEMORY: sqlite-migration-batch-mode).
"""

from typing import Sequence, Union

from alembic import op

from app.engine.match_id_rewrite import affected_tables

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _swap(table: str, column: str, to: str, frm_first: str) -> None:
    """Rewrite ``<prefix>xxxx`` in one column. Escaped LIKE so the ``_`` in the
    two-char prefix is literal, not a wildcard."""
    op.execute(
        f"UPDATE {table} SET {column} = '{to}' || substr({column}, 3) "
        rf"WHERE {column} LIKE '{frm_first}\_%' ESCAPE '\'"
    )


def upgrade() -> None:
    # 1. Value rewrite G_ → M_ (FK enforcement off; columns still named game_id).
    _swap("games", "id", "M_", "G")
    for table in ("players", "turns", "request_incidents"):
        _swap(table, "game_id", "M_", "G")

    # 2. Rename the parent table (SQLite >=3.25 rewrites child FK references;
    #    Postgres keeps them by identity).
    op.rename_table("games", "matches")

    # 3. Column renames (alter_column carries constraints onto the new name).
    with op.batch_alter_table("matches") as b:
        b.alter_column("game_type", new_column_name="game")
    with op.batch_alter_table("players") as b:
        b.alter_column("game_id", new_column_name="match_id")
    with op.batch_alter_table("turns") as b:
        b.alter_column("game_id", new_column_name="match_id")
    with op.batch_alter_table("request_incidents") as b:
        b.alter_column("game_id", new_column_name="match_id")

    # 4. Index renames (SQLite reflects/drops named indexes reliably). Includes
    #    the matches-table indexes whose names still said "games".
    op.drop_index("ix_games_game_type", table_name="matches")
    op.create_index("ix_matches_game", "matches", ["game"])
    op.drop_index("ix_games_scheduled_start", table_name="matches")
    op.create_index("ix_matches_scheduled_start", "matches", ["scheduled_start"])
    op.drop_index("ix_games_state", table_name="matches")
    op.create_index("ix_matches_state", "matches", ["state"])
    op.drop_index("ix_players_game_id", table_name="players")
    op.create_index("ix_players_match_id", "players", ["match_id"])
    op.drop_index("ix_turns_game_id", table_name="turns")
    op.create_index("ix_turns_match_id", "turns", ["match_id"])
    op.drop_index("ix_request_incidents_game_id", table_name="request_incidents")
    op.create_index("ix_request_incidents_match_id", "request_incidents", ["match_id"])

    # Guard: the shared contract with the preview script.
    assert affected_tables()[0] == ("matches", "id")


def downgrade() -> None:
    # Reverse order: rename columns back first, then recreate their indexes, then
    # rename the table, then swap values M_ → G_.
    with op.batch_alter_table("request_incidents") as b:
        b.alter_column("match_id", new_column_name="game_id")
    with op.batch_alter_table("turns") as b:
        b.alter_column("match_id", new_column_name="game_id")
    with op.batch_alter_table("players") as b:
        b.alter_column("match_id", new_column_name="game_id")
    with op.batch_alter_table("matches") as b:
        b.alter_column("game", new_column_name="game_type")

    op.drop_index("ix_request_incidents_match_id", table_name="request_incidents")
    op.create_index("ix_request_incidents_game_id", "request_incidents", ["game_id"])
    op.drop_index("ix_turns_match_id", table_name="turns")
    op.create_index("ix_turns_game_id", "turns", ["game_id"])
    op.drop_index("ix_players_match_id", table_name="players")
    op.create_index("ix_players_game_id", "players", ["game_id"])
    op.drop_index("ix_matches_game", table_name="matches")
    op.create_index("ix_games_game_type", "matches", ["game_type"])
    op.drop_index("ix_matches_scheduled_start", table_name="matches")
    op.create_index("ix_games_scheduled_start", "matches", ["scheduled_start"])
    op.drop_index("ix_matches_state", table_name="matches")
    op.create_index("ix_games_state", "matches", ["state"])

    op.rename_table("matches", "games")

    _swap("games", "id", "G_", "M")
    for table in ("players", "turns", "request_incidents"):
        _swap(table, "game_id", "G_", "M")
