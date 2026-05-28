"""Async SQLAlchemy engine and session factory.

Same code works against SQLite (dev) and Postgres (prod). The connection
string is the only environment difference.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def make_engine(url: str | None = None) -> AsyncEngine:
    """Create an async engine. Override `url` for tests."""
    return create_async_engine(
        url or settings.database_url,
        echo=False,
        future=True,
    )


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
