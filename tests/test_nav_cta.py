"""Smart CTA: label adapts to the visitor's funnel state.

Covers both the pure resolver (`compute_nav_cta`) and the rendered nav, plus the
`/play` smart redirect used by the "Get started" and "Play now" states.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.connection_health import LIVE_WINDOW_SECONDS
from app.main import app
from app.models import Base
from app.models.agent import AgentKind
from app.models.connection import Connection
from app.routes.nav_context import (
    compute_nav_cta,
    user_disconnected_connection_count,
    user_live_connection_count,
)
from tests.factories import make_agent, make_bot, make_connection, make_user


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


def _desktop_nav_html(page: str) -> str:
    """The inline desktop wayfinding row."""
    start = page.index('<div class="al-navlinks mono">')
    return page[start : page.index("</div>", start)]


async def _connect(reset_db: async_sessionmaker, connection_id: int) -> None:
    """Mark a connection as having a current MCP setup.

    The "Play now" bar moved from "connected once" (``first_connected_at``) to
    "has current MCP setup" (a recent ``mcp_connected_at`` for an MCP provider),
    so set both: ``first_connected_at`` for legacy signals and ``mcp_connected_at``
    so the new readiness bar reads CONNECTED_NOT_LIVE.
    """
    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        now = datetime.now(timezone.utc)
        connection.first_connected_at = now
        connection.mcp_connected_at = now
        await db.commit()


# ── pure resolver ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cta_signed_out_is_get_started(reset_db):
    async with reset_db() as db:
        cta = await compute_nav_cta(db, None)
    assert cta.label == "Get started"
    assert cta.href == "/play"


@pytest.mark.asyncio
async def test_cta_no_agent_is_create_agent(reset_db):
    # Agent-first: a brand-new user needs a competitor before connecting one.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Create your agent"
    assert cta.href == "/me/agents/new"


@pytest.mark.asyncio
async def test_cta_connection_no_agent_is_create_agent(reset_db):
    # Has a connection but no agent yet -> still create the agent first.
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Create your agent"
    assert cta.href == "/me/agents/new"


@pytest.mark.asyncio
async def test_cta_unconnected_agent_is_connect(reset_db):
    # Has an agent but it has never connected -> next step is connecting the AI.
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)
        await make_agent(db, user, name="Atlas")  # first_connected_at stays NULL
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Connect your AI"
    assert cta.href == "/me/connections"


@pytest.mark.asyncio
async def test_cta_connected_agent_is_play_now(reset_db):
    # The "Play now" bar is now "has current MCP setup" (provider_readiness >=
    # CONNECTED_NOT_LIVE), so a Claude (MCP) provider needs a recent mcp_connected_at.
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        await make_agent(db, user, connection=connection, name="Atlas")
        connection.first_connected_at = datetime.now(timezone.utc)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"


@pytest.mark.asyncio
async def test_cta_bot_only_is_create_agent(reset_db):
    # A bot (house bot) isn't the visitor's own AI agent, so they still need to
    # create one before anything else.
    async with reset_db() as db:
        user = await make_user(db)
        await make_agent(db, user, name="Sable", kind=AgentKind.BOT, connection=None)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Create your agent"
    assert cta.href == "/me/agents/new"


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
async def test_desktop_nav_renders_primary_links_inline(client):
    r = await client.get("/games")
    assert r.status_code == 200
    desktop_nav = _desktop_nav_html(r.text)
    assert 'href="/games"' in desktop_nav
    assert 'href="/leaderboard"' in desktop_nav


@pytest.mark.asyncio
async def test_home_desktop_nav_keeps_how_it_works_inline(client):
    r = await client.get("/")
    assert r.status_code == 200
    desktop_nav = _desktop_nav_html(r.text)
    assert 'href="/#how"' in desktop_nav


@pytest.mark.asyncio
async def test_nav_renders_play_now_for_connected_user(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        await make_agent(db, user, connection=connection, name="Atlas")
        await db.commit()
        user_id, connection_id = user.id, connection.id
    await _connect(reset_db, connection_id)

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "Play now" in r.text
    assert "Get started" not in r.text


@pytest.mark.asyncio
async def test_nav_renders_create_your_agent_for_user_without_agent(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "Create your agent" in r.text
    assert 'href="/me/agents/new"' in r.text
    assert "Play now" not in r.text


@pytest.mark.asyncio
async def test_nav_renders_create_an_agent_for_user_with_connection_no_agent(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "Create your agent" in r.text
    assert 'href="/me/agents/new"' in r.text
    assert "Connect your AI" not in r.text


# ── /play smart redirect ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_play_signed_out_redirects_to_login(client):
    r = await client.get("/play", follow_redirects=False)
    assert r.status_code == 302
    assert "/auth/google/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_play_unconnected_agent_goes_to_connect(client, reset_db):
    # /play now routes a setup-incomplete user to their next gate (decision 1):
    # an unconnected agent's provider is not set up, so /play sends them to the
    # connect screen instead of dropping them at a lobby they can't act in.
    async with reset_db() as db:
        user = await make_user(db)
        await make_bot(db, user, name="Atlas")  # agent exists, provider never connected
        await db.commit()
        user_id = user.id
    r = await client.get(
        "/play", cookies=_signed_in_cookies(user_id), follow_redirects=False
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/me/connections?provider=claude")
    # /play threads ?next back to itself so the funnel re-enters after connecting.
    assert "next=%2Fplay" in loc


@pytest.mark.asyncio
async def test_play_connected_agent_goes_to_lobby(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        await make_agent(db, user, connection=connection, name="Atlas")
        await db.commit()
        user_id, connection_id = user.id, connection.id
    await _connect(reset_db, connection_id)

    r = await client.get(
        "/play", cookies=_signed_in_cookies(user_id), follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help#lobby-upcoming"


# ── live connection count (nav green dot) ───────────────────────────────────


@pytest.mark.asyncio
async def test_live_connection_count_zero_when_no_connections(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 0


@pytest.mark.asyncio
async def test_live_connection_count_zero_when_connection_never_seen(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)  # last_seen_at stays NULL
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 0


@pytest.mark.asyncio
async def test_live_connection_count_zero_when_connection_stale(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(
            seconds=LIVE_WINDOW_SECONDS + 10
        )
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 0


@pytest.mark.asyncio
async def test_live_connection_count_one_when_connection_warm(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 1


@pytest.mark.asyncio
async def test_disconnected_count_zero_when_no_connections(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        count = await user_disconnected_connection_count(db, user.id)
    assert count == 0


@pytest.mark.asyncio
async def test_disconnected_count_one_when_never_seen(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)  # last_seen_at stays NULL
        await db.commit()
        count = await user_disconnected_connection_count(db, user.id)
    assert count == 1


@pytest.mark.asyncio
async def test_disconnected_count_one_when_stale(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(
            seconds=LIVE_WINDOW_SECONDS + 10
        )
        await db.commit()
        count = await user_disconnected_connection_count(db, user.id)
    assert count == 1


@pytest.mark.asyncio
async def test_disconnected_count_zero_when_warm(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await db.commit()
        count = await user_disconnected_connection_count(db, user.id)
    assert count == 0


@pytest.mark.asyncio
async def test_nav_shows_mixed_counts(client, reset_db):
    # 1 warm, 2 stale — green badge shows 1, red badge shows 2
    async with reset_db() as db:
        user = await make_user(db)
        warm, _ = await make_connection(db, user)
        warm.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        stale1, _ = await make_connection(db, user)
        stale1.last_seen_at = datetime.now(timezone.utc) - timedelta(
            seconds=LIVE_WINDOW_SECONDS + 60
        )
        stale2, _ = await make_connection(db, user)
        stale2.last_seen_at = datetime.now(timezone.utc) - timedelta(
            seconds=LIVE_WINDOW_SECONDS + 120
        )
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "al-acct-badge-live" in r.text
    assert "al-acct-badge-off" in r.text


@pytest.mark.asyncio
async def test_nav_badge_not_green_when_connection_disconnected(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(
            seconds=LIVE_WINDOW_SECONDS + 60
        )
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "al-acct-badge-live" not in r.text
    assert "al-acct-badge-off" in r.text


@pytest.mark.asyncio
async def test_nav_badge_green_when_connection_warm(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "al-acct-badge-live" in r.text
    assert "al-acct-badge-off" not in r.text
