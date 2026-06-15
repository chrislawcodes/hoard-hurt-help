"""Tests for join-before-connect seat holds.

Joining with an agent whose provider is live seats a confirmed Player and goes
to the match. Joining with an agent whose provider is NOT live seats a *held*
Player (``seat_reserved_until`` set) and goes to the connect-countdown page. A
held seat is confirmed when its provider comes online and released when its
deadline passes. A held seat never counts as a real player.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.scheduler import _active_player_count
from app.engine.seat_hold import sweep_held_seats
from app.main import app
from app.models import Base, GameState, Match, Player, User
from tests.factories import make_agent, make_connection, make_user

JOIN_URL = "/games/hoard-hurt-help/matches/G_001/join"


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


async def _user_agent(reset_db, *, live: bool):
    """Make a user + agent whose provider connection is live or not."""
    async with reset_db() as db:
        user = await make_user(db, 0)
        connection, _ = await make_connection(db, user)
        if live:
            connection.last_seen_at = datetime.now(timezone.utc)
        else:
            connection.last_seen_at = None
        agent, _v = await make_agent(db, user, connection=connection, name="Atlas")
        await db.commit()
        return user.id, agent.id


# ---------------------------------------------------------------------------
# Join → held vs confirmed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_live_agent_confirms_and_goes_to_match(client, reset_db):
    await _seed_match(reset_db)
    user_id, agent_id = await _user_agent(reset_db, live=True)
    r = await client.post(
        JOIN_URL,
        data={"agent_id": agent_id},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/G_001"
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalar_one()
        assert player.seat_reserved_until is None  # confirmed


@pytest.mark.asyncio
async def test_join_offline_agent_holds_and_goes_to_countdown(client, reset_db):
    await _seed_match(reset_db)
    user_id, agent_id = await _user_agent(reset_db, live=False)
    r = await client.post(
        JOIN_URL,
        data={"agent_id": agent_id},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalar_one()
        assert player.seat_reserved_until is not None  # held
    assert r.headers["location"] == (
        f"/games/hoard-hurt-help/matches/G_001/connect/{player.id}"
    )


# ---------------------------------------------------------------------------
# Held-seat page forks on setup state (new vs returning), no countdown
# ---------------------------------------------------------------------------


async def _held_seat_for_state(reset_db, *, model: str, with_connection: bool) -> tuple[int, int]:
    """A held seat for an agent. with_connection enables that provider (RETURNING);
    without one the provider is set up nowhere (NEW)."""
    async with reset_db() as db:
        user = await make_user(db, 0)
        connection = None
        if with_connection:
            connection, _ = await make_connection(db, user)  # enabled, offline
        agent, _v = await make_agent(db, user, connection=connection, model=model, name="Atlas")
        player = Player(
            match_id="G_001",
            user_id=user.id,
            agent_id=agent.id,
            seat_name="Atlas",
            seat_reserved_until=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.add(player)
        await db.flush()
        await db.commit()
        return user.id, player.id


@pytest.mark.asyncio
async def test_seat_connect_returning_state_shows_wake_prompt(client, reset_db):
    """Provider set up but offline → wake it with the play-prompt; no countdown."""
    await _seed_match(reset_db)
    uid, pid = await _held_seat_for_state(reset_db, model="claude-haiku-4-5", with_connection=True)
    r = await client.get(
        f"/games/hoard-hurt-help/matches/G_001/connect/{pid}", cookies=_cookies(uid)
    )
    assert r.status_code == 200
    assert "bringing your AI online" in r.text  # no "time to connect" countdown
    assert "already set up" in r.text
    assert "hoardhurthelp MCP tools" in r.text  # the play-prompt to wake it


@pytest.mark.asyncio
async def test_seat_connect_new_state_shows_connect_walkthrough(client, reset_db):
    """Provider set up nowhere → full connect walkthrough, not the play-prompt."""
    await _seed_match(reset_db)
    uid, pid = await _held_seat_for_state(reset_db, model="gemini-3.1-flash-lite", with_connection=False)
    r = await client.get(
        f"/games/hoard-hurt-help/matches/G_001/connect/{pid}", cookies=_cookies(uid)
    )
    assert r.status_code == 200
    assert "Let's connect Gemini" in r.text
    assert "Connect Gemini →" in r.text
    assert "hoardhurthelp MCP tools" not in r.text  # no play-prompt for a new setup


# ---------------------------------------------------------------------------
# Held seats don't count as players
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_held_seat_not_counted_as_active_player(reset_db):
    await _seed_match(reset_db)
    async with reset_db() as db:
        user = await make_user(db, 0)
        agent, _v = await make_agent(db, user, name="Atlas")
        db.add(
            Player(
                match_id="G_001",
                user_id=user.id,
                agent_id=agent.id,
                seat_name="agent0/Atlas",
                seat_reserved_until=datetime.now(timezone.utc) + timedelta(seconds=90),
            )
        )
        await db.commit()
        assert await _active_player_count(db, "G_001") == 0


# ---------------------------------------------------------------------------
# Sweep — confirm when live, release when expired
# ---------------------------------------------------------------------------


async def _held_player(reset_db, *, live: bool, deadline: datetime):
    async with reset_db() as db:
        user = await make_user(db, 0)
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc) if live else None
        agent, _v = await make_agent(db, user, connection=connection, name="Atlas")
        player = Player(
            match_id="G_001",
            user_id=user.id,
            agent_id=agent.id,
            seat_name="agent0/Atlas",
            seat_reserved_until=deadline,
        )
        db.add(player)
        await db.commit()
        return player.id


@pytest.mark.asyncio
async def test_sweep_confirms_seat_when_provider_live(reset_db):
    await _seed_match(reset_db)
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    player_id = await _held_player(reset_db, live=True, deadline=future)
    await sweep_held_seats(reset_db)
    async with reset_db() as db:
        player = (await db.execute(select(Player).where(Player.id == player_id))).scalar_one()
        assert player.seat_reserved_until is None  # confirmed


@pytest.mark.asyncio
async def test_sweep_releases_expired_unconnected_seat(reset_db):
    await _seed_match(reset_db)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    player_id = await _held_player(reset_db, live=False, deadline=past)
    await sweep_held_seats(reset_db)
    async with reset_db() as db:
        gone = (await db.execute(select(Player).where(Player.id == player_id))).first()
        assert gone is None  # released


@pytest.mark.asyncio
async def test_sweep_keeps_unexpired_unconnected_seat(reset_db):
    await _seed_match(reset_db)
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    player_id = await _held_player(reset_db, live=False, deadline=future)
    await sweep_held_seats(reset_db)
    async with reset_db() as db:
        player = (await db.execute(select(Player).where(Player.id == player_id))).scalar_one()
        assert player.seat_reserved_until is not None  # still waiting


# ---------------------------------------------------------------------------
# Connect-countdown status fragment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_waiting_when_not_live(client, reset_db):
    await _seed_match(reset_db)
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    async with reset_db() as db:
        user = await make_user(db, 0)
        agent, _v = await make_agent(db, user, name="Atlas")
        player = Player(
            match_id="G_001",
            user_id=user.id,
            agent_id=agent.id,
            seat_name="agent0/Atlas",
            seat_reserved_until=future,
        )
        db.add(player)
        await db.commit()
        user_id, player_id = user.id, player.id
    r = await client.get(
        f"/games/hoard-hurt-help/matches/G_001/connect/{player_id}/status",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers
    assert "Waiting" in r.text


@pytest.mark.asyncio
async def test_status_hx_redirects_when_live(client, reset_db):
    await _seed_match(reset_db)
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    player_id = await _held_player(reset_db, live=True, deadline=future)
    async with reset_db() as db:
        user_id = (await db.execute(select(User.id))).scalar_one()
    r = await client.get(
        f"/games/hoard-hurt-help/matches/G_001/connect/{player_id}/status",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/games/hoard-hurt-help/matches/G_001"
    async with reset_db() as db:
        player = (await db.execute(select(Player).where(Player.id == player_id))).scalar_one()
        assert player.seat_reserved_until is None


@pytest.mark.asyncio
async def test_status_released_when_expired(client, reset_db):
    await _seed_match(reset_db)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    player_id = await _held_player(reset_db, live=False, deadline=past)
    async with reset_db() as db:
        user_id = (await db.execute(select(User.id))).scalar_one()
    r = await client.get(
        f"/games/hoard-hurt-help/matches/G_001/connect/{player_id}/status",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Seat released" in r.text
    async with reset_db() as db:
        gone = (await db.execute(select(Player).where(Player.id == player_id))).first()
        assert gone is None
