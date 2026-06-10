"""Tests for the live connection handshake (specs/005-bot-onboarding-handshake).

Covers the onboarding state machine, first-connection detection (via the agent
auth choke point), first-move detection, owner-scoping of the new routes, and
correct first paint.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.engine.connection_health import ConnectionHealth, compute_connection_health
from app.engine.connection_activity import (
    bot_channel,
    mark_first_move,
    mark_seen,
)
from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Base, Match, GameState, Player, Turn, TurnSubmission, User
from app.models.agent import Agent
from app.models.connection import Connection, ConnectionStatus
from tests.factories import make_agent, make_connection, make_user, seat_player

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


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
    monkeypatch.setattr("app.routes.agent_api._last_poll", {})
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def events(monkeypatch):
    """Capture every broadcast.publish call as (channel, event_type, payload)."""
    captured: list[tuple[str, str, dict]] = []

    async def fake_publish(channel: str, event_type: str, payload: dict) -> None:
        captured.append((channel, event_type, payload))

    monkeypatch.setattr("app.broadcast.publish", fake_publish)
    return captured


def _signed_in_cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _game(db, gid: str, state: GameState) -> Match:
    g = Match(
        id=gid,
        name=f"Match {gid}",
        state=state,
        scheduled_start=NOW + timedelta(hours=1),
        per_turn_deadline_seconds=60,
    )
    db.add(g)
    await db.flush()
    return g


async def _player(db, match_id: str, agent: Agent, user: User, seat_name: str = "AI_0") -> Player:
    # Pin the seat to the agent's connection so the connection's serving health
    # sees this match (health is pin-based now, not agent-attachment).
    p = Player(
        match_id=match_id,
        user_id=user.id,
        agent_id=agent.id,
        seat_name=seat_name,
        served_by_connection_id=agent.connection_id,
        served_pinned_at=NOW,
    )
    db.add(p)
    await db.flush()
    return p


async def _submission(
    db, match_id: str, player: Player, *, round_: int, turn_: int, defaulted: bool = False
) -> TurnSubmission:
    t = Turn(
        match_id=match_id,
        round=round_,
        turn=turn_,
        turn_token=generate_turn_token(),
        opened_at=NOW,
        deadline_at=NOW + timedelta(minutes=1),
    )
    db.add(t)
    await db.flush()
    s = TurnSubmission(
        turn_id=t.id,
        player_id=player.id,
        action="HOARD",
        was_defaulted=defaulted,
        submitted_at=NOW,
    )
    db.add(s)
    await db.flush()
    return s


# --------------------------------------------------------------------------
# Foundation: connection health states (T004 / T006)
# --------------------------------------------------------------------------


async def test_state_waiting_when_never_connected_no_games(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u, status=ConnectionStatus.PENDING)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.DISCONNECTED
    assert health.never_connected is True
    assert health.needs_reconnect is True


async def test_state_waiting_in_game_when_entered_but_not_connected(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u, status=ConnectionStatus.PENDING)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        g = await _game(db, "G_1", GameState.REGISTERING)
        await _player(db, g.id, agent, u)
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.DISCONNECTED
    assert health.never_connected is True
    assert health.needs_reconnect is True


async def test_state_connected_no_game(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        connection.first_connected_at = NOW
        connection.last_seen_at = NOW
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.READY


async def test_state_connected_pregame(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        connection.first_connected_at = NOW
        connection.last_seen_at = NOW
        g = await _game(db, "G_1", GameState.REGISTERING)
        await _player(db, g.id, agent, u)
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.READY


async def test_state_in_game_no_move(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        connection.stall_threshold = 1
        connection.first_connected_at = NOW
        connection.last_seen_at = NOW
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, agent, u)
        await _submission(db, g.id, p, round_=1, turn_=1, defaulted=True)
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.STALLED
    assert health.match_id == "G_1"


async def test_state_playing_when_moved(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        connection.last_seen_at = NOW
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, agent, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.LIVE
    assert health.match_id == "G_1"


async def test_playing_points_only_at_a_live_game_not_a_finished_one(reset_db):
    # Regression: an agent that played a real move in a game that has since
    # finished is still "playing" (established), but must NOT carry a game link
    # - pointing "Watch live" at a completed game is a dead link.
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        connection.last_seen_at = NOW
        g = await _game(db, "G_1", GameState.COMPLETED)
        p = await _player(db, g.id, agent, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.READY
    assert health.match_id is None
    assert health.game_name is None


async def test_defaulted_submission_does_not_count_as_moved(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        connection.stall_threshold = 1
        connection.first_connected_at = NOW
        connection.last_seen_at = NOW
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, agent, u)
        await _submission(db, g.id, p, round_=1, turn_=1, defaulted=True)
        await db.commit()
        health = await compute_connection_health(db, connection, now=NOW)
    assert health.state is ConnectionHealth.STALLED


# --------------------------------------------------------------------------
# Foundation: signal emission (T005)
# --------------------------------------------------------------------------


async def test_mark_seen_sets_and_publishes_once(reset_db, events):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        await db.commit()

        await mark_seen(db, connection, key_hash=connection.key_lookup)
        first = connection.first_connected_at
        await mark_seen(db, connection, key_hash=connection.key_lookup)  # no second 'connected'

    assert first is not None
    assert connection.first_connected_at == first  # unchanged on second call
    assert connection.last_seen_at is not None  # heartbeat stamped on connect
    connected = [e for e in events if e[1] == "connected"]
    assert connected == [(bot_channel(connection.id), "connected", {})]


async def test_mark_first_move_publishes_only_on_first(reset_db, events):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, agent, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()

        await mark_first_move(db, agent.id)  # exactly one real submission -> publish

        await _submission(db, g.id, p, round_=1, turn_=2)
        await db.commit()
        await mark_first_move(db, agent.id)  # now two -> no publish

    moved = [e for e in events if e[1] == "moved"]
    assert moved == [(bot_channel(agent.id), "moved", {})]


# --------------------------------------------------------------------------
# US1: first connection detected through the agent auth choke point (T012)
# --------------------------------------------------------------------------


async def test_agent_call_records_connection_once(client, reset_db, events):
    async with reset_db() as db:
        await _game(db, "G_1", GameState.REGISTERING)
        p = await seat_player(db, "G_1", "AI_0", i=0)
        await db.commit()
        connection_id, key = p._test_connection.id, p._test_key

    r = await client.get("/api/games/G_1/turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200

    async with reset_db() as db:
        connection = (await db.execute(select(Connection).where(Connection.id == connection_id))).scalar_one()
        assert connection.first_connected_at is not None

    # A second call must not re-publish (idempotent).
    await client.get("/api/games/G_1/turn", headers={"X-Connection-Key": key})
    connected = [e for e in events if e[1] == "connected"]
    assert connected == [(bot_channel(connection_id), "connected", {})]


# --------------------------------------------------------------------------
# US1/US2: the status fragment - owner-scoping, first paint, join guidance
# --------------------------------------------------------------------------


async def test_status_fragment_first_paint_waiting(client, reset_db):
    """Waiting state (never connected): /status fragment renders empty — no card.

    State 1 (waiting / never connected) is already covered by the reconnect card
    in detail.html. The polled /status fragment renders nothing for this state so
    there is no duplication.
    """
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u, status=ConnectionStatus.PENDING)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        await db.commit()
        uid, aid = u.id, agent.id

    r = await client.get(f"/me/agents/{aid}/status", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    # Waiting state renders no card in the polled fragment
    assert "Ready to play" not in r.text
    assert "Playing" not in r.text
    # The reconnect / health info lives on the full detail page, not this fragment


async def test_status_fragment_connected_no_game_shows_join(client, reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        now = datetime.now(timezone.utc)
        connection.first_connected_at = now
        connection.last_seen_at = now
        await db.commit()
        uid, aid = u.id, agent.id

    r = await client.get(f"/me/agents/{aid}/status", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    assert "Ready" in r.text


async def test_detail_empty_games_copy_when_connected(client, reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        now = datetime.now(timezone.utc)
        connection.first_connected_at = now
        connection.last_seen_at = now
        await db.commit()
        uid, aid = u.id, agent.id

    r = await client.get(f"/me/agents/{aid}", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    assert "Ready" in r.text
    assert "Needs connection" not in r.text


async def test_detail_established_agent_shows_playing_card(client, reset_db):
    # An agent that played a real move shows the "Playing" onboarding card.
    # State 5 (PLAYING) is explicitly rendered as a persistent card so the user
    # can see their agent is active and follow the "Watch it play →" link.
    # The health badge continues to show the connection state separately.
    async with reset_db() as db:
        u = await make_user(db)
        connection, _ = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name="Atlas")
        now = datetime.now(timezone.utc)
        connection.first_connected_at = now  # connected once, recently
        connection.last_seen_at = now
        g = await _game(db, "G_1", GameState.COMPLETED)
        p = await _player(db, g.id, agent, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()
        uid, aid = u.id, agent.id

    r = await client.get(f"/me/agents/{aid}", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    # Badge tells the truth about connection state.
    assert "Ready" in r.text
    # Onboarding card confirms the agent has played.
    assert "Playing" in r.text


async def test_status_fragment_owner_only(client, reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 0)
        other = await make_user(db, 1)
        connection, _ = await make_connection(db, owner)
        agent, _ = await make_agent(db, owner, connection=connection, name="Atlas")
        await db.commit()
        aid, other_id = agent.id, other.id

    r = await client.get(f"/me/agents/{aid}/status", cookies=_signed_in_cookies(other_id))
    assert r.status_code == 404


async def test_health_badge_fragment_renders_and_owner_only(client, reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 0)
        other = await make_user(db, 1)
        connection, _ = await make_connection(db, owner, status=ConnectionStatus.PENDING)
        agent, _ = await make_agent(db, owner, connection=connection, name="Atlas")  # never connected
        await db.commit()
        aid, owner_id, other_id = agent.id, owner.id, other.id

    # Owner sees the live badge fragment (the HTMX poll target).
    r = await client.get(
        f"/me/agents/{aid}/health-badge", cookies=_signed_in_cookies(owner_id)
    )
    assert r.status_code == 200
    assert "badge-alert" in r.text
    assert "Disconnected" in r.text

    # Not the owner -> 404, so no one else can poll your agent's status.
    r = await client.get(
        f"/me/agents/{aid}/health-badge", cookies=_signed_in_cookies(other_id)
    )
    assert r.status_code == 404


async def test_stream_rejects_non_owner(client, reset_db):
    # The security guarantee (FR-010): a non-owner cannot open the agent's stream.
    # `_owned_agent` 404s in the dependency, before any streaming begins. The
    # owner's 200 path is an infinite event-stream that ASGITransport can't
    # cleanly consume in-process, so it's exercised in the live preview instead
    # (quickstart), not here.
    async with reset_db() as db:
        owner = await make_user(db, 0)
        other = await make_user(db, 1)
        connection, _ = await make_connection(db, owner)
        agent, _ = await make_agent(db, owner, connection=connection, name="Atlas")
        await db.commit()
        aid, other_id = agent.id, other.id

    r = await client.get(f"/me/agents/{aid}/stream", cookies=_signed_in_cookies(other_id))
    assert r.status_code == 404
