"""Tests for the smart gated Join flow.

The join page is the HUB. On GET it checks setup state and redirects to the
FIRST missing thing, carrying ?next back to the join URL:

  1. Not signed in   → /auth/google/login?next=<join>
  2. No handle       → /me/handle?next=<join>
  3. No AI agent     → /me/agents/new?next=<join>
  4. Has an AI agent → render the join form (no Player seated)

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
async def test_no_agent_redirects_to_create_agent_with_next(client, reset_db):
    await _seed_match(reset_db)
    user = await _user_with_handle(reset_db)  # handle, but no agent at all
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
        await make_connection(db, u)  # enables the claude provider for creation
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
    assert r.headers["location"] == JOIN_URL


@pytest.mark.asyncio
async def test_create_agent_post_rejects_external_next(client, reset_db):
    # An external next is dropped; we fall back to the agent detail page.
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
