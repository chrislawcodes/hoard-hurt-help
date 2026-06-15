from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.engine.mode_a_connection import mode_a_connection_for
from app.deps import assert_connection_usable
from app.models import Base
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.user import User


@pytest.fixture
async def db_session_factory(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield session_factory


async def _make_user(db: AsyncSession, *, suffix: str = "0") -> User:
    user = User(
        google_sub=f"sub-{suffix}",
        email=f"u{suffix}@example.com",
        handle=f"user{suffix}",
        handle_key=f"user{suffix}",
    )
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, deleted_at, expected_status_code, expected_code",
    [
        (ConnectionStatus.ACTIVE, datetime.now(timezone.utc), 410, "CONNECTION_DELETED"),
        (ConnectionStatus.PAUSED, None, 403, "CONNECTION_PAUSED"),
    ],
)
async def test_assert_connection_usable_raises_expected_errors(
    db_session_factory: async_sessionmaker[AsyncSession],
    status: ConnectionStatus,
    deleted_at: datetime | None,
    expected_status_code: int,
    expected_code: str,
) -> None:
    async with db_session_factory() as db:
        user = await _make_user(db)
        connection = Connection(
            user=user,
            provider=None,
            key_lookup="hash",
            key_hint="abcd",
            status=status,
            deleted_at=deleted_at,
            mode_a_at=datetime.now(timezone.utc),
        )
        db.add(connection)
        await db.flush()
        if expected_code == "CONNECTION_DELETED":
            connection.user.disabled_at = None
        with pytest.raises(HTTPException) as exc:
            assert_connection_usable(connection)
        assert exc.value.status_code == expected_status_code
        assert exc.value.detail["error"]["code"] == expected_code


@pytest.mark.asyncio
async def test_assert_connection_usable_rejects_disabled_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as db:
        user = await _make_user(db)
        user.disabled_at = datetime.now(timezone.utc)
        connection = Connection(
            user=user,
            provider=None,
            key_lookup="hash",
            key_hint="abcd",
            status=ConnectionStatus.ACTIVE,
            mode_a_at=datetime.now(timezone.utc),
        )
        db.add(connection)
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            assert_connection_usable(connection)
        assert exc.value.status_code == 403
        assert exc.value.detail["error"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_mode_a_connection_for_concurrent_calls_create_one_row(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as db:
        user = await _make_user(db)
        user_id = user.id
        await db.commit()

    start = asyncio.Event()

    async def claim() -> int:
        await start.wait()
        async with db_session_factory() as db:
            stored_user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            connection = await mode_a_connection_for(
                db, stored_user, provider=ConnectionProvider.GEMINI
            )
            await db.commit()
            return connection.id

    tasks = [asyncio.create_task(claim()) for _ in range(8)]
    start.set()
    connection_ids = await asyncio.gather(*tasks)

    async with db_session_factory() as db:
        rows = (
            await db.execute(
                select(Connection)
                .where(
                    Connection.user_id == user_id,
                    Connection.mode_a_at.is_not(None),
                    Connection.deleted_at.is_(None),
                )
                .order_by(Connection.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        connection = rows[0]
        assert set(connection_ids) == {connection.id}
        assert connection.status is ConnectionStatus.ACTIVE
        assert connection.mode_a_at is not None

        # One MCP client == one provider: only the connecting client's provider
        # (GEMINI here) is enabled, not the whole set.
        provider_rows = (
            await db.execute(
                select(ConnectionProviderRow)
                .where(ConnectionProviderRow.connection_id == connection.id)
                .order_by(ConnectionProviderRow.provider)
            )
        ).scalars().all()
        assert len(provider_rows) == 1
        assert provider_rows[0].provider is ConnectionProvider.GEMINI
        assert provider_rows[0].enabled is True


@pytest.mark.asyncio
async def test_mode_a_connection_enables_only_the_connecting_provider(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as db:
        user = await _make_user(db, suffix="prov")
        connection = await mode_a_connection_for(
            db, user, provider=ConnectionProvider.CLAUDE
        )
        await db.commit()
        rows = (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == connection.id
                )
            )
        ).scalars().all()
        assert {(r.provider, r.enabled) for r in rows} == {
            (ConnectionProvider.CLAUDE, True)
        }


@pytest.mark.asyncio
async def test_mode_a_connection_without_provider_creates_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The bare sign-in token exchange does not know which client connected, so it
    # passes no provider. With no existing connection there is nothing to resolve,
    # so it returns None and creates nothing — the connection is born later, at the
    # MCP handshake, when the provider is known.
    async with db_session_factory() as db:
        user = await _make_user(db, suffix="noprov")
        connection = await mode_a_connection_for(db, user)
        await db.commit()
        assert connection is None
        rows = (
            await db.execute(
                select(Connection).where(Connection.user_id == user.id)
            )
        ).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_mode_a_connection_without_provider_reuses_single_existing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # When the user already has exactly one Mode A connection, a provider-less call
    # (unidentified client) resolves to it rather than guessing or creating.
    async with db_session_factory() as db:
        user = await _make_user(db, suffix="one")
        made = await mode_a_connection_for(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()
        resolved = await mode_a_connection_for(db, user)
        assert resolved is not None
        assert resolved.id == made.id


@pytest.mark.asyncio
async def test_mode_a_connection_without_provider_is_none_when_ambiguous(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # With several connections we cannot pick safely on an unidentified client.
    async with db_session_factory() as db:
        user = await _make_user(db, suffix="many")
        await mode_a_connection_for(db, user, provider=ConnectionProvider.CLAUDE)
        await mode_a_connection_for(db, user, provider=ConnectionProvider.GEMINI)
        await db.commit()
        assert await mode_a_connection_for(db, user) is None


@pytest.mark.asyncio
async def test_mode_a_connection_one_per_provider(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Each provider the user signs in gets its OWN connection — never one
    # connection that accumulates several.
    async with db_session_factory() as db:
        user = await _make_user(db, suffix="perprov")
        gem = await mode_a_connection_for(db, user, provider=ConnectionProvider.GEMINI)
        cla = await mode_a_connection_for(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()
        assert gem.id != cla.id
        assert gem.provider is ConnectionProvider.GEMINI
        assert cla.provider is ConnectionProvider.CLAUDE
        # Each connection carries exactly its own one enabled provider row.
        for conn, prov in [(gem, ConnectionProvider.GEMINI), (cla, ConnectionProvider.CLAUDE)]:
            rows = (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id == conn.id
                    )
                )
            ).scalars().all()
            assert {(r.provider, r.enabled) for r in rows} == {(prov, True)}
        # Two live Mode A connections for the user, one per provider.
        live = (
            await db.execute(
                select(Connection).where(
                    Connection.user_id == user.id,
                    Connection.mode_a_at.is_not(None),
                    Connection.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(live) == 2


@pytest.mark.asyncio
async def test_mode_a_connection_for_resurrects_soft_deleted_row(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as db:
        user = await _make_user(db, suffix="1")
        connection = await mode_a_connection_for(
            db, user, provider=ConnectionProvider.CLAUDE
        )
        await db.commit()
        connection_id = connection.id

    async with db_session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        stored.deleted_at = datetime.now(timezone.utc)
        stored.status = ConnectionStatus.PAUSED
        stored.paused_at = stored.deleted_at
        stored.paused_reason = "deleted"
        await db.commit()

    async with db_session_factory() as db:
        stored_user = (
            await db.execute(select(User).where(User.google_sub == "sub-1"))
        ).scalar_one()
        # Reconnecting the SAME provider resurrects the same row.
        resurrected = await mode_a_connection_for(
            db, stored_user, provider=ConnectionProvider.CLAUDE
        )
        await db.commit()

    assert resurrected.id == connection_id
    assert resurrected.deleted_at is None
    assert resurrected.status is ConnectionStatus.ACTIVE
    assert resurrected.provider is ConnectionProvider.CLAUDE

    async with db_session_factory() as db:
        rows = (
            await db.execute(
                select(Connection)
                .where(
                    Connection.user_id == stored_user.id,
                    Connection.mode_a_at.is_not(None),
                )
                .order_by(Connection.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == connection_id
