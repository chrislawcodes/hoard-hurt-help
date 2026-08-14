"""Async SQLAlchemy engine and session factory.

Same code works against SQLite (dev) and Postgres (prod). The connection
string is the only environment difference.
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.identity.milestone_listeners import install_milestone_listeners
from app.sqlite_parity import install_sqlite_parity_guards

# Reproduce prod (Postgres) write-rejection on SQLite dev/test sessions.
install_sqlite_parity_guards()

# Record engagement milestones from ORM inserts. Registered here at import rather
# than at app startup: the test client mounts the app without a lifespan, so a
# startup-time registration would leave the whole suite running with no listeners
# attached — passing, and proving nothing.
install_milestone_listeners()


def make_engine(url: str | None = None) -> AsyncEngine:
    """Create an async engine. Override `url` for tests."""
    resolved_url = url or settings.database_url
    # Pool sizing is Postgres-only: SQLite (dev + tests) runs on pool classes
    # that accept neither argument, so passing them there is a TypeError.
    pool_options: dict[str, Any] = (
        {}
        if resolved_url.startswith("sqlite")
        else {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
        }
    )
    engine = create_async_engine(
        resolved_url,
        echo=False,
        future=True,
        # Check out connections with a liveness ping. A long-running app (turn
        # scheduler + auto-start poller) otherwise reuses connections Postgres
        # dropped while idle, failing with "connection is closed".
        pool_pre_ping=True,
        **pool_options,
    )
    if resolved_url.startswith("sqlite"):
        # SQLite ignores foreign keys unless this pragma is set per connection.
        # Postgres (prod) always enforces them, so leaving it off lets a delete
        # that orphans a row pass in dev/tests but 500 in prod. Match prod.
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine: AsyncEngine = make_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session
