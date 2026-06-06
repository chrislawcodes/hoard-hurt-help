"""Smart "Play" CTA: label adapts to the visitor's funnel state.

Covers both the pure resolver (`compute_nav_cta`) and the rendered nav, plus the
`/play` smart redirect that the CTA points at.
"""

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.main import app
from app.models import Base, Bot, BotKind
from app.routes.nav_context import compute_nav_cta
from tests.factories import make_bot, make_user


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

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


async def _connect(reset_db: async_sessionmaker, bot_id: int) -> None:
    """Mark an agent as having connected at least once."""
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        bot.first_connected_at = datetime.now(timezone.utc)
        await db.commit()


# ── pure resolver ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cta_signed_out_is_get_started(reset_db):
    async with reset_db() as db:
        cta = await compute_nav_cta(db, None)
    assert cta.label == "Get started"
    assert cta.href == "/play"


@pytest.mark.asyncio
async def test_cta_no_agent_is_connect(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Connect your agent"


@pytest.mark.asyncio
async def test_cta_unconnected_agent_is_connect(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await make_bot(db, user, name="Atlas")  # first_connected_at stays NULL
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Connect your agent"


@pytest.mark.asyncio
async def test_cta_connected_agent_is_play_now(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        bot, _ = await make_bot(db, user, name="Atlas")
        bot.first_connected_at = datetime.now(timezone.utc)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"


@pytest.mark.asyncio
async def test_cta_sim_only_is_connect(reset_db):
    # A connected Sim doesn't count — Sims aren't the visitor's own external agent.
    async with reset_db() as db:
        user = await make_user(db)
        bot, _ = await make_bot(db, user, name="Sable", kind=BotKind.SIM)
        bot.first_connected_at = datetime.now(timezone.utc)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Connect your agent"


# ── rendered nav ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nav_renders_get_started_when_signed_out(client):
    # Interior page: the pill is the single entry — no separate "Sign in" beside it.
    r = await client.get("/games")
    assert r.status_code == 200
    assert 'class="al-nav-cta"' in r.text
    assert "Get started" in r.text
    assert "al-nav-auth" not in r.text


@pytest.mark.asyncio
async def test_home_drops_pill_keeps_signin_when_signed_out(client):
    # Marketing home: the hero is the CTA, so the nav pill is dropped; the bar
    # offers the quiet "Sign in" instead — exactly one entry, no double button.
    r = await client.get("/")
    assert r.status_code == 200
    assert "al-nav-cta" not in r.text
    assert "al-nav-auth" in r.text  # the quiet "Sign in"


@pytest.mark.asyncio
async def test_nav_renders_play_now_for_connected_user(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        bot, _ = await make_bot(db, user, name="Atlas")
        await db.commit()
        user_id, bot_id = user.id, bot.id
    await _connect(reset_db, bot_id)

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "Play now" in r.text
    assert "Get started" not in r.text


# ── /play smart redirect ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_play_signed_out_redirects_to_login(client):
    r = await client.get("/play", follow_redirects=False)
    assert r.status_code == 302
    assert "/auth/google/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_play_unconnected_agent_goes_to_lobby(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await make_bot(db, user, name="Atlas")  # never connected
        await db.commit()
        user_id = user.id
    r = await client.get(
        "/play", cookies=_signed_in_cookies(user_id), follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help"


@pytest.mark.asyncio
async def test_play_connected_agent_goes_to_lobby(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        bot, _ = await make_bot(db, user, name="Atlas")
        await db.commit()
        user_id, bot_id = user.id, bot.id
    await _connect(reset_db, bot_id)

    r = await client.get(
        "/play", cookies=_signed_in_cookies(user_id), follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help"
