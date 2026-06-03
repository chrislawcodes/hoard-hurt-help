"""Tests for graceful key rotation.

Reissue is non-destructive: the previous key keeps authenticating until the new
one is first used, then it's retired — so reconnecting never knocks a running bot
offline. Revoke is the immediate-cutoff path for a leaked key.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.main import app
from app.models import Base, Bot, Match, GameState, Player
from app.routes import agent_api
from tests.factories import make_bot, make_user


def _clear_poll_throttle() -> None:
    """The /turn endpoint rejects rapid polls (429). Clear that between the
    back-to-back auth calls this suite makes so we're testing auth, not cadence."""
    agent_api._last_poll.clear()
    agent_api._last_pull.clear()


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
    monkeypatch.setattr("app.routes.agent_api._last_poll", {})
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _signed_in_cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _bot_in_active_game(reset_db, key: str) -> int:
    """Seat a bot (with plaintext `key`) as a player in an ACTIVE game G_001."""
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
        bot, _ = await make_bot(db, u, key=key)
        db.add(Player(match_id="G_001", user_id=u.id, bot_id=bot.id, agent_id="A"))
        await db.commit()
        return bot.id


async def test_graceful_overlap_old_key_works_until_new_used(client, reset_db):
    key_a = generate_bot_key()
    key_b = generate_bot_key()
    bot_id = await _bot_in_active_game(reset_db, key_a)
    # Simulate a graceful reissue: current = B, previous = A (both valid).
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        bot.key_lookup = bot_key_lookup(key_b)
        bot.key_hint = bot_key_hint(key_b)
        bot.prev_key_lookup = bot_key_lookup(key_a)
        await db.commit()

    # Old key still authenticates (grace window).
    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key_a})
    assert r.status_code == 200
    _clear_poll_throttle()
    # New key authenticates — and retires the old one as a side effect.
    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key_b})
    assert r.status_code == 200
    _clear_poll_throttle()
    # Old key is now dead.
    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key_a})
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "INVALID_KEY"

    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        assert bot.prev_key_lookup is None  # retired after the new key was used


async def test_reissue_route_is_graceful_and_double_safe(client, reset_db):
    key_a = generate_bot_key()
    a_hash = bot_key_lookup(key_a)
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u, key=key_a)
        await db.commit()
        bot_id, user_id = bot.id, u.id
    cookies = _signed_in_cookies(user_id)

    # First reissue: the original key becomes the still-valid previous key.
    r = await client.post(f"/me/bots/{bot_id}/reissue", cookies=cookies)
    assert r.status_code in (302, 303)
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        assert bot.prev_key_lookup == a_hash
        assert bot.key_lookup != a_hash
        after_first = bot.key_lookup

    # Second reissue before the new key is used: prev MUST stay the original
    # (still-valid) key, not the unused pending one — so a running bot on the
    # original key is never orphaned by a double reissue.
    r = await client.post(f"/me/bots/{bot_id}/reissue", cookies=cookies)
    assert r.status_code in (302, 303)
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        assert bot.prev_key_lookup == a_hash
        assert bot.key_lookup not in (a_hash, after_first)


async def test_revoke_route_kills_old_keys_immediately(client, reset_db):
    key_a = generate_bot_key()
    a_hash = bot_key_lookup(key_a)
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u, key=key_a)
        bot.prev_key_lookup = bot_key_lookup("sk_bot_lingering")  # a grace key
        await db.commit()
        bot_id, user_id = bot.id, u.id
    cookies = _signed_in_cookies(user_id)

    r = await client.post(f"/me/bots/{bot_id}/revoke", cookies=cookies)
    assert r.status_code in (302, 303)
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        assert bot.prev_key_lookup is None  # grace key cut off immediately
        assert bot.key_lookup != a_hash  # a fresh current key was issued
