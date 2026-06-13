"""Sign-in connection bootstrap: the /me/connections page should flip to
"connected" when a client initializes its MCP session, not only after the first
tool call. See mcp_server.server.SigninConnectionMiddleware.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastmcp.server.dependencies import AccessToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import Base
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from mcp_server import server


def _token(*, sub: str = "sub-123", email: str = "agent@example.com") -> AccessToken:
    return AccessToken(
        token="access-token-1",
        client_id=sub,
        scopes=["openid", "email", "profile"],
        subject=sub,
        claims={
            "sub": sub,
            "email": email,
            "name": "Agent One",
            "email_verified": True,
        },
    )


@pytest.fixture
async def db_session_factory(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield session_factory


@pytest.mark.asyncio
async def test_signin_creates_active_connection_without_counting_a_call(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A fresh sign-in yields a live, agent-ready connection — and the handshake
    is not billed as an API call (that is what separates it from a tool call)."""
    async with db_session_factory() as db:
        _access, _userinfo, connection = await server._connection_from_token(db, _token())
        await db.commit()

        # Live + connected -> the page shows "connected", not "waiting".
        assert connection.status == ConnectionStatus.ACTIVE
        assert connection.first_connected_at is not None
        # The handshake is not a paid model inference.
        assert connection.api_call_count == 0

        # Every provider is enabled, so the user can create agents right away.
        provider_rows = (
            (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id == connection.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert provider_rows
        assert all(row.enabled for row in provider_rows)


@pytest.mark.asyncio
async def test_tool_path_still_records_the_call(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The tool path is unchanged: it records the authenticated call (cost
    signal), unlike the sign-in handshake."""
    async with db_session_factory() as db:
        _access, _userinfo, connection = await server._resolve_oauth_connection(db, _token())
        refreshed = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert refreshed.status == ConnectionStatus.ACTIVE
        assert refreshed.api_call_count == 1


@pytest.mark.asyncio
async def test_signin_is_idempotent_one_connection_per_user(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Repeated sign-ins (e.g. reconnects) reuse the same connection, never
    spawning duplicates."""
    async with db_session_factory() as db:
        _a1, _u1, first = await server._connection_from_token(db, _token())
        await db.commit()
        _a2, _u2, second = await server._connection_from_token(db, _token())
        await db.commit()

        assert first.id == second.id
        live = (
            (
                await db.execute(
                    select(Connection).where(
                        Connection.deleted_at.is_(None),
                        Connection.mode_a_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(live) == 1


@pytest.mark.asyncio
async def test_initialize_middleware_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bootstrap failure must not break the session — initialize still runs."""
    monkeypatch.setattr(server, "get_access_token", lambda: _token())

    async def _boom(token: object) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(server, "_bootstrap_signin_connection", _boom)

    seen = {}

    async def _call_next(context: object) -> str:
        seen["called"] = True
        return "initialized"

    middleware = server.SigninConnectionMiddleware()
    result = await middleware.on_initialize(object(), _call_next)  # type: ignore[arg-type]

    assert result == "initialized"
    assert seen["called"] is True


@pytest.mark.asyncio
async def test_initialize_middleware_skips_when_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No token (discovery / pre-auth) -> no bootstrap attempt, session proceeds."""
    monkeypatch.setattr(server, "get_access_token", lambda: None)

    bootstrap_called = {"value": False}

    async def _bootstrap(token: object) -> None:
        bootstrap_called["value"] = True

    monkeypatch.setattr(server, "_bootstrap_signin_connection", _bootstrap)

    async def _call_next(context: object) -> str:
        return "initialized"

    middleware = server.SigninConnectionMiddleware()
    result = await middleware.on_initialize(object(), _call_next)  # type: ignore[arg-type]

    assert result == "initialized"
    assert bootstrap_called["value"] is False
