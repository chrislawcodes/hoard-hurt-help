"""Tests for the gated Join flow.

The join page leads with "Play as yourself" (a human seat), so it always renders
for a signed-in user with a handle — even one with zero agents and zero
connections. Only the identity gates still redirect on GET:

  1. Not signed in           → /auth/google/login?next=<join>
  2. No handle               → /me/handle?next=<join>
  3. Signed in + handle      → render the join form (no Player seated)

A user with no AI agent is NOT bounced away anymore: they land on the join form
and can play as a human in one click. The AI-agent picker below is the opt-in
path — it shows ALL of the user's AI agents (including ones whose provider is
offline or not set up), so an unconnected provider no longer blocks the screen;
they pick it and connect on the next screen.

It also tests that each existing page HONORS ?next (forwards on completion) and
that ?next is validated as an internal path (no open redirect).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Base, GameState, Match, Player, User
from app.routes.web_support import safe_internal_next
from tests.factories import make_agent, make_connection, make_user
from tests.conftest import signed_in_cookies as _cookies

JOIN_URL = "/games/hoard-hurt-help/matches/G_001/join"
JOIN_NEXT = quote(JOIN_URL, safe="")


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


async def _seed_match(reset_db, state: GameState = GameState.REGISTERING) -> None:
    async with reset_db() as db:
        db.add(
            Match(
                id="G_001",
                name="Test Match",
                state=state,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                per_turn_deadline_seconds=60,
            )
        )
        await db.commit()


async def _user_with_handle(reset_db, *, i: int = 0) -> User:
    async with reset_db() as db:
        user = await make_user(db, i)
        await db.commit()
        await db.refresh(user)
        return user


async def _seated_players(reset_db) -> int:
    async with reset_db() as db:
        rows = (await db.execute(select(Player).where(Player.match_id == "G_001"))).all()
        return len(rows)


# ---------------------------------------------------------------------------
# safe_internal_next — open-redirect guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/games/hoard-hurt-help/matches/G_001/join", "/games/hoard-hurt-help/matches/G_001/join"),
        ("/me/agents", "/me/agents"),
        (None, None),
        ("", None),
        ("https://evil.example.com", None),
        ("http://evil.example.com", None),
        ("//evil.example.com", None),
        (r"/\evil.example.com", None),
        ("javascript:alert(1)", None),
        ("relative/path", None),
    ],
)
def test_safe_internal_next_rejects_external(raw, expected) -> None:
    assert safe_internal_next(raw) == expected


# ---------------------------------------------------------------------------
# Gate ordering on the join hub
# ---------------------------------------------------------------------------


async def test_not_signed_in_redirects_to_login_with_next(client, reset_db):
    await _seed_match(reset_db)
    r = await client.get(JOIN_URL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/auth/google/login?next={JOIN_URL}"


async def test_no_handle_redirects_to_handle_with_next(client, reset_db):
    await _seed_match(reset_db)
    async with reset_db() as db:
        user = await make_user(db, 0)
        user.handle = None
        user.handle_key = None
        await db.commit()
        await db.refresh(user)
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/me/handle?next=")
    assert JOIN_NEXT in loc


async def test_fresh_user_no_connection_lands_on_join_form_as_human(client, reset_db):
    # Brand-new user: handle, but ZERO connections and ZERO agents. They are NOT
    # bounced to setup — the join form renders with "Play as yourself" leading,
    # and the AI path offers to create an agent.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)  # handle, no connection, no agent
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Play as yourself" in r.text
    assert "Create one" in r.text  # the AI path, gated behind a missing agent
    assert await _seated_players(reset_db) == 0  # GET seats nobody


async def test_provider_but_no_agent_lands_on_join_form_as_human(client, reset_db):
    # A connected provider but no agent yet: still no bounce. The join form renders
    # with the human option; the AI path waits until they create an agent.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_connection(db, u)  # enables a provider, but no agent created
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Play as yourself" in r.text
    assert await _seated_players(reset_db) == 0


async def test_agent_without_any_connection_shows_form_not_connected(client, reset_db):
    # With no connection at all, the agent still SHOWS on the form and the overall
    # status reads "No AI connected" — the user can pick it and connect on the next
    # screen instead of being bounced away.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_agent(db, u, name="Atlas")  # no connection => unconfigured
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Atlas" in r.text
    # No connection at all → every AI in the picker offers to be set up.
    assert "not connected" in r.text


async def test_agent_but_stale_connection_shows_form_not_running(client, reset_db):
    # Provider enabled on a connection but the connection is stale (never seen) =>
    # not live. The form still renders, showing the provider as "Not running".
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        await make_agent(db, u, connection=connection, name="Atlas")
        connection.mcp_connected_at = datetime.now(timezone.utc)
        connection.first_connected_at = connection.mcp_connected_at
        connection.last_seen_at = None  # never heartbeated => not live
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Atlas" in r.text
    # Connected but never heartbeated → the AI shows as set-up-but-idle.
    assert "○ idle" in r.text


async def test_create_agent_from_join_returns_and_shows_agent(client, reset_db):
    # A user with a (live) machine but no agent lands on the join form (human
    # option). The AI path's "create an agent" link carries ?next; creating the
    # agent forwards straight back, and now it shows as a pickable AI agent.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.last_seen_at = datetime.now(timezone.utc)  # LIVE machine, no agent yet
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()
    cookies = _cookies(user.id)

    # No agent yet: the join form still renders, leading with the human option.
    r1 = await client.get(JOIN_URL, cookies=cookies, follow_redirects=False)
    assert r1.status_code == 200
    assert "Play as yourself" in r1.text

    # Create the agent with that next -> forwards straight back to the join screen.
    r2 = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "Play to win.",
            "next": JOIN_URL,
        },
        cookies=cookies,
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == JOIN_URL

    # Back at the join screen: the new agent now shows as a pickable AI option.
    r3 = await client.get(JOIN_URL, cookies=cookies, follow_redirects=False)
    assert r3.status_code == 200
    assert "Atlas" in r3.text
    assert await _seated_players(reset_db) == 0


async def test_all_set_renders_join_form_and_seats_nothing(client, reset_db):
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        await make_agent(db, u, connection=connection, name="Atlas")
        connection.last_seen_at = datetime.now(timezone.utc)  # LIVE
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Atlas" in r.text
    # Visiting the hub never seats a Player — no half-join when backing out.
    assert await _seated_players(reset_db) == 0


# ---------------------------------------------------------------------------
# Existing pages HONOR ?next
# ---------------------------------------------------------------------------


async def test_create_agent_form_carries_next_hidden_field(client, reset_db):
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_connection(db, u)  # so a provider is enabled (model is creatable)
        await db.commit()
    r = await client.get(
        f"/me/agents/new?next={JOIN_NEXT}", cookies=_cookies(user.id)
    )
    assert r.status_code == 200
    assert 'name="next"' in r.text
    assert JOIN_URL in r.text


async def test_create_agent_post_forwards_to_next(client, reset_db):
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)  # set up via a recent MCP connection
        connection.mcp_connected_at = datetime.now(timezone.utc)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "Play to win.",
            "next": JOIN_URL,
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Claude is set up (enabled), so creation forwards straight to the next hop.
    assert r.headers["location"] == JOIN_URL


async def test_create_agent_post_rejects_external_next(client, reset_db):
    # An external next is dropped; we fall back to the lobby (not the external
    # target). This is the security property: the evil URL never reaches Location.
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "strategy_text": "Play to win.",
            "next": "https://evil.example.com",
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help"
    assert "evil.example.com" not in r.headers["location"]


async def test_connections_page_forwards_to_next_when_already_live(client, reset_db):
    # Reaching /me/connections?next=... while a connection is already live jumps
    # straight back to the join hub instead of showing the "Connected" box.
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.last_seen_at = datetime.now(timezone.utc)  # LIVE
        await db.commit()
    r = await client.get(
        f"/me/connections?next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == JOIN_URL


async def test_connect_target_provider_not_bounced_by_a_different_live_provider(
    client, reset_db
):
    """The bug: hitting /me/connections?provider=gemini while a DIFFERENT provider
    (Claude) is live must NOT bounce straight back. Gemini isn't connected, so the
    page leads with the Gemini connect steps instead of 'you're all set'."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)  # Claude, by default
        connection.last_seen_at = datetime.now(timezone.utc)  # Claude is LIVE
        await db.commit()
    r = await client.get(
        f"/me/connections?provider=gemini&next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200  # rendered, NOT bounced back
    # Leads with the Gemini-specific MCP setup path, not "you're set". Gemini is
    # IDE-only now (Antigravity), so it shows the paste-in serverUrl config block.
    assert "Connect Gemini" in r.text
    assert "serverUrl" in r.text  # the Antigravity paste-in config block
    assert "X-Connection-Key" not in r.text


async def test_connect_gemini_status_ignores_a_playing_claude(client, reset_db):
    """On a Connect-Gemini page, the live/playing status must reflect GEMINI only —
    a Claude that's live and playing must NOT make it say 'Your AI is playing'."""
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)  # Claude, by default
        connection.last_seen_at = datetime.now(timezone.utc)  # live
        connection.api_call_count = 5  # and playing (made real game calls)
        await db.commit()
    r = await client.get(
        f"/me/connections?provider=gemini&next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Your AI is playing" not in r.text  # that's Claude, not Gemini
    assert "Connect Gemini" in r.text


async def test_connect_target_shows_mcp_setup_without_self_setup_key(client, reset_db):
    """The connect-a-provider page leads with MCP setup, not the raw HTTP
    self-setup key prompt."""
    user = await _user_with_handle(reset_db)
    r = await client.get(
        f"/me/connections?provider=gemini&next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    gemini_block = r.text.split("byo-panel-gemini", 1)[1].split("</section>", 1)[0]
    # Gemini is IDE-only (Antigravity): a copyable serverUrl config block plus the
    # click-Authenticate step — no terminal command, no self-setup key.
    assert "serverUrl" in gemini_block
    assert "Authenticate" in gemini_block
    assert "gemini mcp add" not in gemini_block
    assert "sk_conn_" not in gemini_block
    assert "/api/agent/next-turn" not in gemini_block
    assert "X-Connection-Key" not in gemini_block


async def test_connections_page_waits_when_not_live(client, reset_db):
    # Not live yet: render the page (no forward), and the poll fragment carries
    # ?next so it can forward once the AI connects.
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.last_seen_at = None  # not live
        await db.commit()
    r = await client.get(
        f"/me/connections?next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    # The polled fragment carries next so it can forward later.
    assert "/me/connections/live-status?next=" in r.text


async def test_live_status_fragment_hx_redirects_when_live(client, reset_db):
    # The 4s poll: once live, it answers with HX-Redirect to ?next so HTMX
    # navigates the whole page back to the join hub.
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.last_seen_at = datetime.now(timezone.utc)  # LIVE
        await db.commit()
    r = await client.get(
        f"/me/connections/live-status?next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == JOIN_URL


async def test_live_status_fragment_external_next_is_ignored(client, reset_db):
    # An external next is dropped: no HX-Redirect leaks an open redirect.
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.last_seen_at = datetime.now(timezone.utc)  # LIVE
        await db.commit()
    r = await client.get(
        "/me/connections/live-status?next=https://evil.example.com",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers
