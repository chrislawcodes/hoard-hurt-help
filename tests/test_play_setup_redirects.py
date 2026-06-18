"""Tests for the /play hub and agents_create post-create routing.

Covers:
  - /play is dumb: signed-out → sign-in; any signed-in user → the lobby,
    regardless of setup state. All setup gating moved to the join flow.
  - agents_create POST: provider not set up → /me/connections?provider=...; provider
    set up (recent mcp_connected_at) → agent detail / next.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.main import app
from app.models import Base, User
from tests.factories import make_agent, make_connection, make_user


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _user_with_handle(reset_db, *, i: int = 0) -> User:
    async with reset_db() as db:
        user = await make_user(db, i)
        await db.commit()
        await db.refresh(user)
        return user


# ---------------------------------------------------------------------------
# /play redirect ladder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_signed_out_redirects_to_login(client, reset_db):
    r = await client.get("/play", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/google/login")


@pytest.mark.asyncio
async def test_play_no_handle_goes_to_lobby(client, reset_db):
    # /play is dumb now: a signed-in user lands on the lobby even with no handle.
    # The handle gate moved to the join flow.
    async with reset_db() as db:
        user = await make_user(db, 0)
        user.handle = None
        user.handle_key = None
        await db.commit()
        await db.refresh(user)
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help#lobby-upcoming"


@pytest.mark.asyncio
async def test_play_no_agent_goes_to_lobby(client, reset_db):
    # Signed in with a handle but no agent → still the lobby (no smart funnel).
    user = await _user_with_handle(reset_db)
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help#lobby-upcoming"


@pytest.mark.asyncio
async def test_play_with_live_agent_redirects_to_lobby(client, reset_db):
    # Signed in with handle + agent + live provider → lobby anchor.
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        connection.last_seen_at = datetime.now(timezone.utc)
        connection.last_polled_at = datetime.now(timezone.utc)
        await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    assert "lobby" in r.headers["location"]


# ---------------------------------------------------------------------------
# /play is dumb: agent + NO connected provider still goes to the lobby. The
# provider gate now lives on the join flow, not on /play.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_agent_but_no_mcp_connection_goes_to_lobby(client, reset_db):
    """User has handle + agent but NO MCP connection → still the lobby.

    The /play smart funnel was removed; the provider gate is enforced on the
    join flow instead (see test_join_seat_hold / _join_setup_redirect coverage).
    """
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_agent(db, u, name="Atlas")
        await db.commit()
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help#lobby-upcoming"


# ---------------------------------------------------------------------------
# Loop-guard: seen-but-not-polling fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_seen_not_polling_user_gets_lobby_not_cycle(client, reset_db):
    """A user whose connection is seen recently but has a stale last_polled_at
    (SEEN_NOT_POLLING readiness) is NOT stuck in a redirect cycle.

    The resolver maps SEEN_NOT_POLLING → NEEDS_LIVE → above NEEDS_MCP_CONNECTION,
    so with the default require bar (NEEDS_MCP_CONNECTION) the stage resolves to
    READY and next_url is the lobby anchor. Assert /play delivers the lobby.
    """
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        now = datetime.now(timezone.utc)
        # mcp_connected_at recent + last_seen recent → has_current_setup = True,
        # has_live_current_setup = True, but last_polled stale → loop_running = False
        # → ProviderReadiness.SEEN_NOT_POLLING
        connection.mcp_connected_at = now - timedelta(days=1)
        connection.last_seen_at = now - timedelta(seconds=30)  # within LIVE_WINDOW
        connection.last_polled_at = now - timedelta(hours=1)  # stale → not polling
        await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    # SEEN_NOT_POLLING is above NEEDS_MCP_CONNECTION → READY → lobby
    assert "lobby" in loc
    # Must not be a setup URL
    assert "/me/connections" not in loc
    assert "/me/agents" not in loc
    assert "/me/handle" not in loc


@pytest.mark.asyncio
async def test_ready_user_never_redirected_to_setup_url(client, reset_db):
    """READY user invariant: /play must never send a fully-set-up user to a setup gate."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        now = datetime.now(timezone.utc)
        connection.mcp_connected_at = now
        connection.last_seen_at = now
        connection.last_polled_at = now
        await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    setup_prefixes = ("/me/connections", "/me/agents", "/me/handle", "/auth/")
    for prefix in setup_prefixes:
        assert not loc.startswith(prefix), f"READY user redirected to setup URL: {loc}"


# ---------------------------------------------------------------------------
# agents_create POST routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_provider_not_setup_redirects_to_connections(client, reset_db):
    """POST /me/agents/new when the user has no connection at all → /me/connections."""
    user = await _user_with_handle(reset_db)
    # No connection at all → NO_MCP_CONNECTION
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "MyBot",
            "strategy_text": "Play to win.",
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    # The connect page is generic now — no provider scoping.
    assert loc == "/me/connections"


@pytest.mark.asyncio
async def test_create_agent_provider_not_setup_carries_next_to_connections(client, reset_db):
    """The ?next param is threaded through to /me/connections when present."""
    user = await _user_with_handle(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "MyBot",
            "strategy_text": "Play to win.",
            "next": "/me/matches",
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "/me/connections" in loc
    assert "provider=" not in loc
    assert "next=" in loc
    assert "evil" not in loc


@pytest.mark.asyncio
async def test_create_agent_provider_setup_recent_mcp_redirects_to_next(client, reset_db):
    """POST /me/agents/new when provider has a recent mcp_connected_at → next URL."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        # Satisfy provider_has_recent_mcp_connection (the MCP-first gate)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "MyBot",
            "model": "claude-haiku-4-5",
            "strategy_text": "Play to win.",
            "next": "/me/matches",
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    # Provider IS set up → goes to next, not connections
    assert loc == "/me/matches"


@pytest.mark.asyncio
async def test_create_agent_provider_setup_no_next_redirects_to_agent_detail(client, reset_db):
    """POST /me/agents/new with provider set up and no next → /me/agents/<id>."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "MyBot",
            "model": "claude-haiku-4-5",
            "strategy_text": "Play to win.",
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/me/agents/")
    assert "connections" not in loc
