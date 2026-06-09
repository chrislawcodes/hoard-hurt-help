"""Startup helpers for Alembic-managed databases.

The app normally runs ``alembic upgrade head`` on boot. Some older databases
were created from metadata before Alembic tracked them, so they already have the
schema but an empty ``alembic_version`` table. In that case Alembic would try to
recreate revision 0001 and crash on "table already exists".

This module detects that legacy shape and stamps the appropriate pre-rename
revision before applying the normal upgrade path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

logger = logging.getLogger(__name__)

LEGACY_PRE_RENAME_REVISION = "0017"


def _sync_database_url(database_url: str) -> str:
    """Return the sync URL Alembic expects."""
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def detect_legacy_revision(database_url: str) -> str | None:
    """Return the revision to stamp for an unversioned legacy database.

    We only intervene when the schema already exists but Alembic has no
    revision recorded. A fresh database has no application tables yet, so
    Alembic should still run from 0001.
    """
    sync_url = _sync_database_url(database_url)
    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())
            if "alembic_version" in table_names:
                version_count = conn.execute(
                    text("SELECT count(*) FROM alembic_version")
                ).scalar_one()
                if version_count != 0:
                    return None
            elif not table_names:
                return None

            if "matches" in table_names and "games" not in table_names:
                return "0018"
            if "games" in table_names and "matches" not in table_names:
                return LEGACY_PRE_RENAME_REVISION
            return None
    finally:
        engine.dispose()


def _cancel_active_games_if_schema_pending(config: Config, database_url: str) -> None:
    """Cancel ACTIVE games when there are pending schema migrations.

    A destructive migration (e.g. one that drops and recreates the players table)
    wipes player data and leaves games in an unrecoverable zombie state. Cancelling
    them before the upgrade is cleaner — the match shows as cancelled rather than
    stuck-active with no players.

    No-op when the database is already at head (normal restarts with no pending
    migrations must not touch running games).
    """
    sync_url = _sync_database_url(database_url)
    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        head = ScriptDirectory.from_config(config).get_current_head()
        if current is None or current == head:
            return

        with engine.connect() as conn:
            if "matches" not in set(inspect(conn).get_table_names()):
                return
            rows = conn.execute(
                text("SELECT id FROM matches WHERE state = 'active'")
            ).fetchall()
            if not rows:
                return
            active_ids = [r[0] for r in rows]
            conn.execute(
                text(
                    "UPDATE matches SET state = 'cancelled', cancelled_at = :now"
                    " WHERE state = 'active'"
                ),
                {"now": datetime.now(timezone.utc).isoformat()},
            )
            conn.commit()
        # Loud and specific: a pending migration may be destructive (e.g. it
        # drops/recreates the players table), which would leave these games as
        # unrecoverable zombies. We name every cancelled match and the reason so
        # the cancellation is never silent. (There is no per-match reason column
        # on `matches`; if one is ever added, also record `reason` there.)
        logger.error(
            "pre-migration guard: CANCELLED %d active match(es) before applying "
            "pending schema migration %s -> %s. reason=pending_schema_migration "
            "(a destructive migration could wipe player data and strand these "
            "matches as zombies). match_ids=%s",
            len(active_ids),
            current,
            head,
            active_ids,
        )
    finally:
        engine.dispose()


_REQUIRED_TABLES = ("connection_setups",)


def verify_required_tables(database_url: str) -> None:
    """Raise RuntimeError if any required table is missing after migrations.

    This is a post-migration sanity check.  If the table is absent it means
    the migration that creates it never ran (or the DB was partially rolled
    back), and the app would otherwise fail silently at runtime.  Loud failure
    here is intentional: run ``alembic upgrade head`` to fix it.
    """
    sync_url = _sync_database_url(database_url)
    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            existing = set(inspect(conn).get_table_names())
    finally:
        engine.dispose()

    missing = [t for t in _REQUIRED_TABLES if t not in existing]
    if missing:
        raise RuntimeError(
            f"Required database table(s) missing after migrations: {missing}. "
            "Run 'alembic upgrade head' to apply all pending migrations, "
            "then restart the application."
        )


def prepare_database_for_upgrade(config: Config, database_url: str) -> None:
    """Stamp a legacy unversioned database, then let Alembic upgrade normally."""
    revision = detect_legacy_revision(database_url)
    if revision is not None:
        command.stamp(config, revision)
    _cancel_active_games_if_schema_pending(config, database_url)
