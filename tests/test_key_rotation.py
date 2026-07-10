"""Tests for graceful key rotation.

Rotate is non-destructive: the previous key keeps authenticating until the new
one is first used, then it's retired — so reconnecting never knocks a running
agent offline.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.main import app
from app.models import Match, GameState, Player
from app.models.connection import Connection
from tests.factories import make_agent, make_connection, make_user
from tests.conftest import signed_in_cookies as _signed_in_cookies


@pytest.fixture(autouse=True)
def _autouse_fast_long_poll(fast_long_poll: None) -> None:
    """next-turn long-polls in an active game with no open turn; every test in
    this file expects the real hold skipped, matching this file's old autouse
    `reset_db` fixture.
    """


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
