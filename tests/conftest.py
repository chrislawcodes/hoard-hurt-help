"""Pytest fixtures shared across the test suite.

- Async event loop config via pytest-asyncio (asyncio_mode = "auto" in pyproject).
- In-memory SQLite engine, fresh per test.
- FastAPI TestClient bound to the in-memory engine.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator
import secrets

import pytest
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.user import User
from app.routes.admin_api import router as admin_api_router
from app.routes.admin_web import router as admin_web_router
from app.routes.auth import router as auth_router
from app.routes.handle_web import router as handle_web_router
from app.routes.nav_context import populate_nav_cta
from app.routes.spectator_api import router as spectator_api_router
from app.routes.web import router as web_router

# The production app mounts only the API/runner routes. The legacy web tests
# still exercise the browser surface, so mount the missing routers once for the
# shared test app.
from app.main import app as test_app

test_app.include_router(auth_router)
test_app.include_router(handle_web_router)
test_app.include_router(web_router, dependencies=[Depends(populate_nav_cta)])
test_app.include_router(spectator_api_router)
test_app.include_router(admin_web_router)
test_app.include_router(admin_api_router)


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
        connection_id=None if connection is None else connection.id,
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
