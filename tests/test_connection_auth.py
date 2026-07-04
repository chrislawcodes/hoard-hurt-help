"""Connection auth regression tests."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.engine.tokens import bot_key_lookup
from app.models import Base
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.connection_setup import ConnectionSetup
from app.models.user import User
from app.routes.agent_next_turn import router as agent_next_turn_router


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def app(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.db.engine", engine)
    test_app = FastAPI()
    test_app.include_router(agent_next_turn_router)
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_connection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    key: str | None = None,
) -> tuple[Connection, str]:
    async with session_factory() as db:
        user = await make_user(db)
        connection, plain_key = await make_connection(db, user, status=status, key=key)
        await db.commit()
        return connection, plain_key


async def make_user(db: AsyncSession, i: int = 0) -> User:
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


async def make_connection_setup(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    nickname: str | None = None,
    key: str | None = None,
) -> tuple[ConnectionSetup, str]:
    plain_key = key or f"sk_conn_{secrets.token_hex(24)}"
    setup = ConnectionSetup(
        user_id=user.id,
        provider=provider,
        nickname=nickname,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=plain_key[-4:],
    )
    db.add(setup)
    await db.flush()
    return setup, plain_key


async def test_valid_key_resolves_connection_and_marks_seen(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    connection, key = await _seed_connection(session_factory)

    r1 = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r1.status_code == 200
    assert r1.json()["status"] == "no_game"

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        first_connected_at = stored.first_connected_at
        last_seen_at = stored.last_seen_at
        assert first_connected_at is not None
        assert last_seen_at is not None

    r2 = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r2.status_code == 200
    assert r2.json()["status"] == "no_game"

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert stored.first_connected_at == first_connected_at
        # Two polls back-to-back fall inside the heartbeat throttle window, so
        # last_seen_at must not move — even though api_call_count bumped twice.
        assert stored.last_seen_at == last_seen_at
        assert stored.api_call_count == 2


async def test_first_key_use_creates_connection_from_setup(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        setup, key = await make_connection_setup(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            nickname="My Claude",
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200
    assert r.json()["status"] == "no_game"

    async with session_factory() as db:
        stored_setup = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.id == setup.id))
        ).scalar_one()
        assert stored_setup.connection_id is not None
        assert stored_setup.completed_at is not None
        connection = (
            await db.execute(select(Connection).where(Connection.id == stored_setup.connection_id))
        ).scalar_one()
        assert connection.user_id == user.id
        assert connection.nickname == "My Claude"
        assert connection.first_connected_at is not None
        assert connection.status is ConnectionStatus.ACTIVE


async def test_missing_and_invalid_key_reject(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_connection(session_factory)

    missing = await client.get("/api/agent/next-turn")
    assert missing.status_code == 401
    assert missing.json()["detail"]["error"]["code"] == "INVALID_KEY"

    invalid = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": "sk_conn_bogus"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["detail"]["error"]["code"] == "INVALID_KEY"


async def test_paused_connection_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, key = await _seed_connection(session_factory, status=ConnectionStatus.PAUSED)

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "CONNECTION_PAUSED"


async def test_deleted_connection_returns_gone(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    connection, key = await _seed_connection(session_factory)

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        stored.deleted_at = datetime.now(timezone.utc)
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 410
    assert r.json()["detail"]["error"]["code"] == "CONNECTION_DELETED"


async def test_graceful_rotation_overlap_is_retired_on_new_key_use(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    connection, old_key = await _seed_connection(session_factory)
    new_key = f"sk_conn_{secrets.token_hex(24)}"

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        stored.key_lookup = bot_key_lookup(new_key)
        stored.prev_key_lookup = bot_key_lookup(old_key)
        await db.commit()

    old_ok = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": old_key})
    assert old_ok.status_code == 200

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert stored.prev_key_lookup == bot_key_lookup(old_key)

    new_ok = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": new_key})
    assert new_ok.status_code == 200

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert stored.prev_key_lookup is None

    old_dead = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": old_key},
    )
    assert old_dead.status_code == 401
    assert old_dead.json()["detail"]["error"]["code"] == "INVALID_KEY"
