"""Smart CTA: label adapts to the visitor's funnel state.

Covers both the pure resolver (`compute_nav_cta`) and the rendered nav, plus the
`/play` smart redirect used by the "Get started" and "Play now" states.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.connection_health import LIVE_WINDOW_SECONDS
from app.models.agent import AgentKind
from app.models.connection import Connection
from app.routes.nav_context import (
    compute_nav_cta,
    user_live_connection_count,
)
from tests.factories import make_agent, make_bot, make_connection, make_user
from tests.conftest import signed_in_cookies as _signed_in_cookies


@pytest.fixture(autouse=True)
async def reset_db(reset_db: async_sessionmaker) -> async_sessionmaker:
    """Autouse override of tests/conftest.py's reset_db: every test here touches the DB."""
    return reset_db


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


async def test_cta_signed_out_is_get_started(reset_db):
    async with reset_db() as db:
        cta = await compute_nav_cta(db, None)
    assert cta.label == "Get started"
    assert cta.href == "/play"


async def test_cta_signed_in_no_agent_is_play_now(reset_db):
    # The nav is dumb now: a signed-in user always gets "Play now" → lobby,
    # regardless of setup state. All gating moved to the join flow.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"


async def test_cta_signed_in_connection_no_agent_is_play_now(reset_db):
    # Has a connection but no agent yet -> still just "Play now" (no smart funnel).
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"


async def test_cta_signed_in_unconnected_agent_is_play_now(reset_db):
    # Has an agent but it has never connected -> still "Play now" (no smart funnel).
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)
        await make_agent(db, user, name="Atlas")  # first_connected_at stays NULL
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"


async def test_cta_connected_agent_is_play_now(reset_db):
    # A fully set-up user also gets "Play now" → lobby.
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        await make_agent(db, user, connection=connection, name="Atlas")
        connection.first_connected_at = datetime.now(timezone.utc)
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"


async def test_cta_signed_in_bot_only_is_play_now(reset_db):
    # A bot-only user has no seatable agent, but the nav still just says "Play now".
    async with reset_db() as db:
        user = await make_user(db)
        await make_agent(db, user, name="Sable", kind=AgentKind.BOT, connection=None)
        await db.commit()
        cta = await compute_nav_cta(db, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"


# ── rendered nav ────────────────────────────────────────────────────────────


async def test_nav_renders_get_started_when_signed_out(client):
    # Interior page: the pill is the single entry — no separate "Sign in" beside it.
    r = await client.get("/games")
    assert r.status_code == 200
    assert 'class="al-nav-cta"' in r.text
    assert "Get started" in r.text
    assert "al-nav-auth" not in r.text


async def test_home_drops_pill_keeps_signin_when_signed_out(client):
    # Marketing home: the hero is the CTA, so the nav pill is dropped; the bar
    # offers the quiet "Sign in" instead — exactly one entry, no double button.
    r = await client.get("/")
    assert r.status_code == 200
    assert "al-nav-cta" not in r.text
    assert "al-nav-auth" in r.text  # the quiet "Sign in"


async def test_desktop_nav_renders_primary_links_inline(client):
    r = await client.get("/games")
    assert r.status_code == 200
    desktop_nav = _desktop_nav_html(r.text)
    assert 'href="/games"' in desktop_nav
    assert 'href="/leaderboard"' in desktop_nav


async def test_home_desktop_nav_keeps_how_it_works_inline(client):
    r = await client.get("/")
    assert r.status_code == 200
    desktop_nav = _desktop_nav_html(r.text)
    assert 'href="/#how"' in desktop_nav


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


async def test_nav_renders_play_now_for_user_without_agent(client, reset_db):
    # The nav is dumb: even a brand-new signed-in user sees "Play now" → lobby.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "Play now" in r.text
    assert 'href="/games/hoard-hurt-help#lobby-upcoming"' in r.text
    assert "Create your agent" not in r.text
    assert "Get started" not in r.text


async def test_nav_renders_play_now_for_user_with_connection_no_agent(client, reset_db):
    # A connection-but-no-agent user also just sees "Play now" — no smart funnel.
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "Play now" in r.text
    assert 'href="/games/hoard-hurt-help#lobby-upcoming"' in r.text
    assert "Create your agent" not in r.text
    assert "Connect your AI" not in r.text


# ── /play smart redirect ────────────────────────────────────────────────────


async def test_play_signed_out_redirects_to_login(client):
    r = await client.get("/play", follow_redirects=False)
    assert r.status_code == 302
    assert "/auth/google/login" in r.headers["location"]


async def test_play_setup_incomplete_user_goes_to_lobby(client, reset_db):
    # /play is dumb now: a signed-in user always lands on the lobby, even with
    # setup incomplete. The handle/agent/connection gating moved to the join flow.
    async with reset_db() as db:
        user = await make_user(db)
        await make_bot(db, user, name="Atlas")  # agent exists, provider never connected
        await db.commit()
        user_id = user.id
    r = await client.get(
        "/play", cookies=_signed_in_cookies(user_id), follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/games/hoard-hurt-help#lobby-upcoming"


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


async def test_live_connection_count_zero_when_no_connections(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 0


async def test_live_connection_count_zero_when_connection_never_seen(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user)  # last_seen_at stays NULL
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 0


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


async def test_live_connection_count_one_when_connection_warm(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await db.commit()
        count = await user_live_connection_count(db, user.id)
    assert count == 1


async def test_nav_shows_green_badge_for_warm_provider(client, reset_db):
    # 1 warm connection → green badge shows, no red badge
    async with reset_db() as db:
        user = await make_user(db)
        warm, _ = await make_connection(db, user)
        warm.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    assert "al-acct-badge-live" in r.text
    assert "al-acct-badge-off" not in r.text


async def test_nav_badge_absent_when_connection_stale(client, reset_db):
    # Stale connection → no badge at all (not live, no red dot)
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
    assert "al-acct-badge-off" not in r.text


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
