"""Tests for graceful key rotation.

Rotate is non-destructive: the previous key keeps authenticating until the new
one is first used, then it's retired — so reconnecting never knocks a running
agent offline.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastmcp.server.auth.auth import AccessToken
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.main import app
from app.models import Base, Match, GameState, Player
from app.models.connection import Connection
from tests.factories import make_agent, make_connection, make_user
from tests.conftest import signed_in_cookies as _signed_in_cookies


# Bespoke: also resets agent_api._last_pull and zeroes the long-poll hold for this
# file's next-turn polling tests, so it can't delegate to tests/conftest.py's shared
# reset_db.
@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})
    # next-turn long-polls in an active game with no open turn; return at once so
    # these back-to-back auth probes don't wait out a real hold.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0)
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _bot_in_active_game(reset_db, key: str) -> int:
    """Seat an agent (with plaintext `key`) as a player in an ACTIVE game G_001."""
    async with reset_db() as db:
        g = Match(
            id="G_001",
            name="t",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        u = await make_user(db)
        connection, _ = await make_connection(db, u, key=key)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        db.add(Player(match_id="G_001", user_id=u.id, agent_id=agent.id, seat_name="A"))
        await db.commit()
        return connection.id


async def test_graceful_overlap_old_key_works_until_new_used(client, reset_db):
    key_a = generate_connection_key()
    key_b = generate_connection_key()
    connection_id = await _bot_in_active_game(reset_db, key_a)
    # Simulate a graceful rotation: current = B, previous = A (both valid).
    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        connection.key_lookup = bot_key_lookup(key_b)
        connection.key_hint = bot_key_hint(key_b)
        connection.prev_key_lookup = bot_key_lookup(key_a)
        await db.commit()

    # Old key still authenticates (grace window).
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_a})
    assert r.status_code == 200
    # New key authenticates — and retires the old one as a side effect.
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_b})
    assert r.status_code == 200
    # Old key is now dead.
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_a})
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "INVALID_KEY"

    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        assert connection.prev_key_lookup is None  # retired after the new key was used


async def _rotate_with_overlap(reset_db, connection_id: int, new_key: str, old_key: str) -> None:
    """Put a connection mid-rotation: current = `new_key`, previous = `old_key`."""
    async with reset_db() as db:
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        connection.key_lookup = bot_key_lookup(new_key)
        connection.key_hint = bot_key_hint(new_key)
        connection.prev_key_lookup = bot_key_lookup(old_key)
        await db.commit()


def _oauth_token() -> AccessToken:
    """A Google sign-in token: no connection key anywhere in it."""
    return AccessToken(
        token="mcp-jwt-not-a-connection-key",
        client_id="sub-rotation",
        scopes=["openid", "email", "profile"],
        subject="sub-rotation",
        claims={"sub": "sub-rotation", "email": "rotator@example.com", "name": "Rot"},
    )


async def _prev_key_lookup(reset_db, connection_id: int) -> str | None:
    async with reset_db() as db:
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        return connection.prev_key_lookup


async def test_mcp_oauth_call_leaves_the_rotation_grace_period_alone(
    client, reset_db, monkeypatch
):
    """A keyless /mcp call must not retire a still-valid previous key.

    The regression this pins: ``_resolve_oauth_connection`` used to hand
    ``mark_seen`` the connection's own stored ``key_lookup``, which made the
    cutover test true by construction. One OAuth tool call — which presents a JWT
    and no key at all — then retired the previous key, and a connector still
    running on it started 401ing mid-match and silently idled.

    Runs the real ``_resolve_oauth_connection``, because the bug lived in what
    that function passes, not in ``mark_seen``'s arithmetic.
    """
    from mcp_server import connection_identity

    key_a = generate_connection_key()
    key_b = generate_connection_key()
    connection_id = await _bot_in_active_game(reset_db, key_a)
    await _rotate_with_overlap(reset_db, connection_id, new_key=key_b, old_key=key_a)

    async def fake_sync_google_user(db, userinfo, **_kwargs):
        connection = (
            await db.execute(
                select(Connection)
                .options(joinedload(Connection.user))
                .where(Connection.id == connection_id)
            )
        ).scalar_one()
        return connection.user

    async def fake_mcp_connection_for(db, user, *, provider=None, oauth_client_id=None):
        # The user's MCP connection is this same row — an OAuth client and a
        # running connector share one connection, which is what makes the two
        # credential paths collide.
        return (
            await db.execute(
                select(Connection)
                .options(joinedload(Connection.user))
                .where(Connection.id == connection_id)
            )
        ).scalar_one()

    monkeypatch.setattr(connection_identity, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(connection_identity, "mcp_connection_for", fake_mcp_connection_for)

    async with reset_db() as db:
        await connection_identity._resolve_oauth_connection(db, _oauth_token())

    # The grace period survives, so the connector on the old key keeps working.
    assert await _prev_key_lookup(reset_db, connection_id) == bot_key_lookup(key_a)
    still_ok = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_a})
    assert still_ok.status_code == 200

    # And the thing that DOES end it still does: presenting the new key.
    used_new = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_b})
    assert used_new.status_code == 200
    assert await _prev_key_lookup(reset_db, connection_id) is None
    dead = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_a})
    assert dead.status_code == 401
    assert dead.json()["detail"]["error"]["code"] == "INVALID_KEY"


async def test_mcp_key_signin_with_the_new_key_ends_the_grace_period(client, reset_db):
    """The other half of the contract: a /mcp call that DOES present a key.

    Key sign-in presents a real ``sk_conn_`` key as the bearer, so first use of a
    freshly issued key over /mcp has to retire the old one exactly as it does over
    HTTP. Guards against over-correcting the OAuth bug by making every /mcp call
    keyless.
    """
    from mcp_server import connection_identity, key_auth

    key_a = generate_connection_key()
    key_b = generate_connection_key()
    connection_id = await _bot_in_active_game(reset_db, key_a)
    await _rotate_with_overlap(reset_db, connection_id, new_key=key_b, old_key=key_a)

    # What key_auth mints once it has verified key_b: the raw key IS the token,
    # and the claim names the connection it authenticated as.
    key_token = AccessToken(
        token=key_b,
        client_id=f"connection-key:{connection_id}",
        scopes=["openid", "email", "profile"],
        subject="sub-rotation",
        claims={key_auth.CONNECTION_ID_CLAIM: connection_id},
    )

    async with reset_db() as db:
        await connection_identity._resolve_oauth_connection(db, key_token)

    assert await _prev_key_lookup(reset_db, connection_id) is None
    dead = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key_a})
    assert dead.status_code == 401


async def test_rotate_route_is_graceful_and_double_safe(client, reset_db):
    key_a = generate_connection_key()
    a_hash = bot_key_lookup(key_a)
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u, key=key_a)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
        connection_id, user_id = connection.id, u.id
    cookies = _signed_in_cookies(user_id)

    # First rotate: the original key becomes the still-valid previous key.
    r = await client.post(f"/me/connections/{connection_id}/rotate", cookies=cookies)
    assert r.status_code in (302, 303)
    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        assert connection.prev_key_lookup == a_hash
        assert connection.key_lookup != a_hash
        after_first = connection.key_lookup

    # Second rotate before the new key is used: prev MUST stay the original
    # (still-valid) key, not the unused pending one — so a running bot on the
    # original key is never orphaned by a double rotation.
    r = await client.post(f"/me/connections/{connection_id}/rotate", cookies=cookies)
    assert r.status_code in (302, 303)
    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        assert connection.prev_key_lookup == a_hash
        assert connection.key_lookup not in (a_hash, after_first)


async def test_rotate_route_issues_fresh_key_without_cutoff(client, reset_db):
    key_a = generate_connection_key()
    a_hash = bot_key_lookup(key_a)
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u, key=key_a)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
        connection_id, user_id = connection.id, u.id
    cookies = _signed_in_cookies(user_id)

    r = await client.post(f"/me/connections/{connection_id}/rotate", cookies=cookies)
    assert r.status_code in (302, 303)
    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        assert connection.prev_key_lookup == a_hash
        assert connection.key_lookup != a_hash
