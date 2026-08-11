"""Engine pool sizing.

The app's connection pool is a hard ceiling on how many callers can touch the
DB at once — long-poll holds, the per-match turn loops, and ordinary web
requests all draw from it. Left to SQLAlchemy's defaults it is 15, which these
tests pin as a deliberate, configurable number instead of an accident.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db import make_engine

_PG_URL = "postgresql+asyncpg://user:pw@localhost:5432/db"


def test_postgres_pool_size_follows_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`db_pool_size` / `db_max_overflow` reach the engine, so the ceiling can be
    raised in the environment without a redeploy."""
    monkeypatch.setattr(settings, "db_pool_size", 7)
    monkeypatch.setattr(settings, "db_max_overflow", 3)

    engine = make_engine(_PG_URL)

    assert engine.pool.size() == 7
    # QueuePool exposes no public getter for the overflow cap.
    assert engine.pool._max_overflow == 3


def test_sqlite_engine_builds_without_pool_sizing() -> None:
    """SQLite (dev + tests) runs on pool classes that accept neither argument, so
    make_engine must not pass them — constructing the engine is the assertion."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")

    assert engine.dialect.name == "sqlite"
