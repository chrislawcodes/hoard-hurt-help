"""Tests for the /play hub and agents_create post-create routing.

Covers:
  - /play is dumb: signed-out → sign-in; any signed-in user → the lobby,
    regardless of setup state. All setup gating moved to the join flow.
  - agents_create POST: after create, go to ?next if present, else the lobby —
    never a /me/connections detour (the agent exists regardless of setup).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.models import User
from tests.factories import make_agent, make_connection, make_user


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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


async def test_play_signed_out_redirects_to_login(client, reset_db):
    r = await client.get("/play", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/google/login")


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


async def test_play_no_agent_goes_to_lobby(client, reset_db):
    # Signed in with a handle but no agent → still the lobby (no smart funnel).
    user = await _user_with_handle(reset_db)
    r = await client.get("/play", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help#lobby-upcoming"


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


async def test_create_agent_no_next_no_connection_goes_to_lobby(client, reset_db):
    """POST /me/agents/new with no ?next and no connection → the game lobby.

    We don't route to /me/connections any more: the agent exists regardless of
    setup, and joining a game from the lobby walks the user through connecting."""
    user = await _user_with_handle(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={"name": "MyBot", "strategy_text": "Play to win."},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help"


async def test_create_agent_with_next_returns_to_next(client, reset_db):
    """The ?next destination wins after create (e.g. back to the join the user
    came from), regardless of connection state — no /me/connections detour."""
    user = await _user_with_handle(reset_db)
    next_url = "/games/hoard-hurt-help/matches/G_001/join"
    r = await client.post(
        "/me/agents/new",
        data={"name": "MyBot", "strategy_text": "Play to win.", "next": next_url},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc == next_url
    assert "/me/connections" not in loc
    assert "evil" not in loc


async def test_create_agent_with_connection_and_next_returns_to_next(client, reset_db):
    """Even with a live connection, ?next still wins (no agent-detail/lobby detour)."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={"name": "MyBot", "strategy_text": "Play to win.", "next": "/me/matches"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/me/matches"


async def test_create_agent_with_connection_no_next_goes_to_lobby(client, reset_db):
    """With a connection but no ?next, create lands on the lobby (not the agent page)."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={"name": "MyBot", "strategy_text": "Play to win."},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help"
