"""Pytest fixtures shared across the test suite.

- Async event loop config via pytest-asyncio (asyncio_mode = "auto" in pyproject).
- In-memory SQLite engine, fresh per test.
- FastAPI TestClient bound to the in-memory engine.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.user import User


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _clear_process_caches() -> None:
    """Reset the process-wide read caches before each test.

    These caches are keyed by query params (or a fixed key), not by DB, so
    without this a cached result from one test's in-memory DB would leak into
    the next.
    """
    from app.read_models.leaderboard_cache import clear_leaderboard_cache
    from app.routes.showcase_replay import clear_showcase_replay_cache

    clear_leaderboard_cache()
    clear_showcase_replay_cache()


@pytest.fixture(autouse=True)
def _restore_game_registry() -> Iterator[None]:
    """Snapshot the game registry and restore it after each test.

    Some tests register stub game modules (e.g. inside a test body). Without
    this, those stubs leak into every later test in the run, causing
    order-dependent failures (a leaked stub without `config_defaults()` makes
    registry helpers raise AttributeError). Restoring the snapshot keeps each
    test's registry changes from escaping. Module-scoped registration fixtures
    register before this runs, so their stubs are part of the snapshot and
    survive until their own teardown.
    """
    import app.games as registry

    snapshot = dict(registry._REGISTRY)
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(snapshot)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Fresh in-memory SQLite engine per test."""
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def make_user(db: AsyncSession, i: int = 0) -> User:
    """Create a unique user for tests."""
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@t.com",
        handle=f"agent{i}",
        handle_key=f"agent{i}",
    )
    db.add(user)
    await db.flush()
    return user


async def make_connection(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    nickname: str | None = None,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    key: str | None = None,
) -> tuple[Connection, str]:
    """Create a connection plus its plaintext key."""
    plain_key = key or f"sk_conn_{secrets.token_hex(24)}"
    connection = Connection(
        user_id=user.id,
        nickname=nickname,
        provider=provider,
        key_lookup=hashlib.sha256(plain_key.encode("utf-8")).hexdigest(),
        key_hint=plain_key[-4:],
        status=status,
    )
    db.add(connection)
    await db.flush()
    db.add(
        ConnectionProviderRow(
            connection_id=connection.id,
            provider=provider,
            enabled=True,
            detected=False,
        )
    )
    await db.flush()
    return connection, plain_key


async def make_agent(
    db: AsyncSession,
    user: User,
    *,
    connection: Connection | None = None,
    name: str | None = None,
    kind: AgentKind = AgentKind.AI,
) -> Agent:
    """Create an agent for tests."""
    agent = Agent(
        user_id=user.id,
        provider=connection.provider if connection is not None else None,
        kind=kind,
        name=name or f"agent-{user.id}",
    )
    db.add(agent)
    await db.flush()
    return agent


async def make_agent_version(
    db: AsyncSession,
    agent: Agent,
    *,
    version_no: int = 1,
    model: str = "claude-haiku-4-5",
    strategy_text: str = "Default strategy.",
) -> AgentVersion:
    """Create an immutable agent version for tests."""
    agent_version = AgentVersion(
        agent_id=agent.id,
        version_no=version_no,
        model=model,
        strategy_text=strategy_text,
    )
    db.add(agent_version)
    await db.flush()
    return agent_version
