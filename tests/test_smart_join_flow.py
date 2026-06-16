"""Tests for the smart gated Join flow.

The join page is the HUB. On GET it checks setup state and redirects to the
FIRST missing thing, carrying ?next back to the join URL:

  1. Not signed in           → /auth/google/login?next=<join>
  2. No handle               → /me/handle?next=<join>
  3. No agent                → /me/agents/new?next=<join>   (design the agent)
  4. Has no live provider     → /me/connections?provider=<x>&next=<join>
  5. Has an AI agent         → render the join form (no Player seated)

Creating an agent no longer requires an already-connected provider, so a
brand-new user (zero connections, zero agents) is sent to create an agent
first. The follow-up step is the provider-specific connect flow for that agent.

The form now shows ALL of the user's AI agents grouped by provider — including
ones whose provider is offline or not set up — so an unconnected provider no
longer bounces the user away; they can pick it and connect on the next screen.

It also tests that each existing page HONORS ?next (forwards on completion) and
that ?next is validated as an internal path (no open redirect).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.main import app
from app.models import Base, GameState, Match, Player, User
from app.routes.web_support import safe_internal_next
from tests.factories import make_agent, make_connection, make_user

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


@pytest.mark.asyncio
async def test_not_signed_in_redirects_to_login_with_next(client, reset_db):
    await _seed_match(reset_db)
    r = await client.get(JOIN_URL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/auth/google/login?next={JOIN_URL}"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_fresh_user_no_connection_redirects_to_create_agent_with_next(client, reset_db):
    # Brand-new user: handle, but ZERO connections and ZERO agents. The hub
    # sends them to design an agent first, not to /me/connections.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)  # handle, no connection, no agent
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/me/agents/new?next=")
    assert JOIN_NEXT in loc


@pytest.mark.asyncio
async def test_provider_but_no_agent_redirects_to_create_agent_with_next(client, reset_db):
    # A connected provider but no agent yet: the hub sends them to create one,
    # carrying ?next back to the join URL.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_connection(db, u)  # enables a provider, but no agent created
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/me/agents/new?next=")
    assert JOIN_NEXT in loc


@pytest.mark.asyncio
async def test_agent_without_any_connection_shows_form_not_connected(client, reset_db):
    # An agent whose provider is enabled on NO connection now SHOWS on the form,
    # grouped under its provider as "Not connected" — the user can pick it and
    # connect on the next screen instead of being bounced away.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_agent(db, u, name="Atlas")  # no connection => unconfigured
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Atlas" in r.text
    assert "Not connected" in r.text


@pytest.mark.asyncio
async def test_agent_but_stale_connection_shows_form_not_running(client, reset_db):
    # Provider enabled on a connection but the connection is stale (never seen) =>
    # not live. The form still renders, showing the provider as "Not running".
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        await make_agent(db, u, connection=connection, name="Atlas")
        connection.last_seen_at = None  # never heartbeated => not live
        await db.commit()
    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Atlas" in r.text
    assert "Not running" in r.text


@pytest.mark.asyncio
async def test_hub_chains_from_create_agent_to_connections_no_loop(client, reset_db):
    # End-to-end of the gate chain with no loop: a user who has a (live) machine
    # but no agent hits gate 1 (create agent); creating it forwards back to the
    # hub, which now finds a seatable + live agent and renders the join form.
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u)
        connection.last_seen_at = datetime.now(timezone.utc)  # LIVE machine, no agent yet
        await db.commit()
    cookies = _cookies(user.id)

    # Gate 1: no seatable agent -> create-agent, carrying next.
    r1 = await client.get(JOIN_URL, cookies=cookies, follow_redirects=False)
    assert r1.status_code == 303
    assert r1.headers["location"].startswith("/me/agents/new?next=")

    # Create the agent with that next -> forwards straight back to the join hub.
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

    # Back at the hub: now seatable AND live -> the join form renders (no loop).
    r3 = await client.get(JOIN_URL, cookies=cookies, follow_redirects=False)
    assert r3.status_code == 200
    assert "Atlas" in r3.text
    assert await _seated_players(reset_db) == 0


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_create_agent_post_forwards_to_next(client, reset_db):
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_connection(db, u)  # provider is enabled but not live yet
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


@pytest.mark.asyncio
async def test_create_agent_post_rejects_external_next(client, reset_db):
    # An external next is dropped; since the provider IS set up, we fall back to
    # the agent's detail page (not the external target).
    user = await _user_with_handle(reset_db)
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        await make_connection(db, u)
        await db.commit()
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "Play to win.",
            "next": "https://evil.example.com",
        },
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/me/agents/")
    assert "evil.example.com" not in r.headers["location"]


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
    # Leads with the Gemini-specific path (the self-setup prompt), not "you're set".
    assert "Play with Gemini" in r.text
    assert "X-Connection-Key" in r.text  # the self-setup prompt is shown


@pytest.mark.asyncio
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
    assert "Play with Gemini" in r.text


def test_self_setup_prompt_contract():
    """The self-setup prompt must carry the full play contract: the key, the
    get/submit endpoints, a move example, and server-driven timing/stop."""
    from app.routes.connections_connect_guide import self_setup_play_prompt

    p = self_setup_play_prompt("sk_conn_deadbeef")
    assert "sk_conn_deadbeef" in p
    assert "X-Connection-Key" in p
    assert "/api/agent/next-turn" in p
    assert "/submit" in p and "agent_turn_token" in p
    assert "HOARD" in p and "HURT" in p  # a concrete move example
    assert "platform" in p  # framed as a platform, not one game
    # Timing is server-driven: obey the wait number, stop only when told.
    assert "next_poll_after_seconds" in p  # respect the server's wait hint
    assert "should_stop" in p  # stop is the server's call, not a self-timer
    assert "right away" not in p  # no "ask again right away" busy loop
    # The prompt must not bake in its own timing rule (server owns pacing).
    assert "10 minutes" not in p


@pytest.mark.asyncio
async def test_connect_target_shows_self_setup_prompt_with_a_key(client, reset_db):
    """The connect-a-provider page leads with the AI self-setup prompt: a real
    sk_conn_ key + the HTTP play loop, so the AI can set itself up and play."""
    user = await _user_with_handle(reset_db)
    r = await client.get(
        f"/me/connections?provider=gemini&next={JOIN_NEXT}",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "sk_conn_" in r.text  # a usable key is embedded
    assert "/api/agent/next-turn" in r.text  # the loop contract
    assert "Easiest" in r.text


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
