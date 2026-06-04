"""Startup helpers for Alembic-managed databases.

The app normally runs ``alembic upgrade head`` on boot. Some older databases
were created from metadata before Alembic tracked them, so they already have the
schema but an empty ``alembic_version`` table. In that case Alembic would try to
recreate revision 0001 and crash on "table already exists".

This module detects that legacy shape and stamps the appropriate pre-rename
revision before applying the normal upgrade path.
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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


def prepare_database_for_upgrade(config: Config, database_url: str) -> None:
    """Stamp a legacy unversioned database, then let Alembic upgrade normally."""
    revision = detect_legacy_revision(database_url)
    if revision is not None:
        command.stamp(config, revision)
