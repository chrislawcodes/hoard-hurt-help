"""Feature 017 — a user joins one match as a human *and* with an AI agent.

The data model already allows two seats for one user (a `kind=human` agent seat
and a `kind=AI` agent seat are different `agent_id`s). These cover the join
handler branching that lets both be created in one submit, plus capacity, the
AI-not-live hold, and idempotence.
"""

from __future__ import annotations

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
from app.models import Base, GameState, Match, Player
from app.models.agent import Agent, AgentKind
from app.models.connection import ConnectionProvider
from app.models.match import MatchKind
from tests.factories import make_agent, make_connection, make_user

GAME = "hoard-hurt-help"
JOIN_URL = f"/games/{GAME}/matches/M_0001/join"


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _make_match(
    db,
    match_id: str = "M_0001",
    *,
    state: GameState = GameState.REGISTERING,
    max_players: int = 20,
    match_kind: str | None = None,
) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game=GAME,
        state=state,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        max_players=max_players,
        match_kind=match_kind,
    )
    db.add(match)
    await db.flush()
    return match


async def _add_agent(db, user, *, name: str = "Hawk", live: bool = True) -> Agent:
    """One AI agent for *user*. ``live`` makes its provider ready (seat confirms);
    otherwise the connection exists but isn't running (the seat will be held)."""
    connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
    agent, _ = await make_agent(db, user, connection=connection, name=name)
    now = datetime.now(timezone.utc)
    connection.mcp_connected_at = now
    connection.first_connected_at = now
    if live:
        connection.last_seen_at = now
        connection.last_polled_at = now  # running the play loop → LIVE → not held
    await db.commit()
    return agent


def _human_and_ai(player_rows: list[Player], agents: dict[int, Agent]) -> tuple[int, int]:
    kinds = [agents[p.agent_id].kind for p in player_rows]
    return kinds.count(AgentKind.HUMAN), kinds.count(AgentKind.AI)


async def _agents_by_id(db) -> dict[int, Agent]:
    rows = (await db.execute(select(Agent))).scalars().all()
    return {a.id: a for a in rows}


@pytest.mark.asyncio
async def test_human_and_agent_in_one_submit(reset_db, client):
    """play_as=human + an agent + a live AI seats BOTH: a human seat and an AI seat."""
    async with reset_db() as db:
        user = await make_user(db)  # handle "agent0"
        await _make_match(db)
        agent = await _add_agent(db, user, name="Hawk", live=True)
        agent_id = agent.id

    r = await client.post(
        JOIN_URL,
        data={"play_as": "human", "agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/games/{GAME}/matches/M_0001"

    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.match_id == "M_0001")))
            .scalars()
            .all()
        )
        agents = await _agents_by_id(db)
    assert len(players) == 2
    assert all(p.user_id == user.id for p in players)
    humans, ais = _human_and_ai(players, agents)
    assert humans == 1 and ais == 1
    seat_names = {p.seat_name for p in players}
    assert "agent0" in seat_names  # the human seat = the user's handle
    assert "Hawk" in seat_names  # the AI seat = the agent's name
    assert all(p.seat_reserved_until is None for p in players)  # both active


@pytest.mark.asyncio
async def test_human_only_still_one_seat(reset_db, client):
    """Regression: play_as=human with no agent seats exactly one human player."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db)
        await db.commit()

    r = await client.post(
        JOIN_URL,
        data={"play_as": "human"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        agents = await _agents_by_id(db)
    assert len(players) == 1
    assert _human_and_ai(players, agents) == (1, 0)


@pytest.mark.asyncio
async def test_agent_only_unchanged(reset_db, client):
    """Regression: an agent with no play_as seats exactly one AI player, no human."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db)
        agent = await _add_agent(db, user, live=True)
        agent_id = agent.id

    r = await client.post(
        JOIN_URL,
        data={"agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        agents = await _agents_by_id(db)
    assert len(players) == 1
    assert _human_and_ai(players, agents) == (0, 1)


@pytest.mark.asyncio
async def test_neither_selected_is_400(reset_db, client):
    """Posting with no human and no agent is a friendly error, creating no seat."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db)
        await db.commit()

    r = await client.post(
        JOIN_URL,
        data={"play_as": "ai"},  # no agent_id → nothing chosen
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    async with reset_db() as db:
        count = len((await db.execute(select(Player))).scalars().all())
    assert count == 0


@pytest.mark.asyncio
async def test_both_but_only_one_slot_is_409_and_creates_nothing(reset_db, client):
    """If a human + an agent would overflow max_players, neither seat is created."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db, max_players=1)
        agent = await _add_agent(db, user, live=True)
        agent_id = agent.id

    r = await client.post(
        JOIN_URL,
        data={"play_as": "human", "agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    async with reset_db() as db:
        count = len((await db.execute(select(Player))).scalars().all())
    assert count == 0  # all-or-nothing: the agent seat rolled back with the human


@pytest.mark.asyncio
async def test_both_with_unconnected_ai_holds_agent_human_active(reset_db, client):
    """When the chosen AI isn't live, its seat is held and the user is routed to
    connect it — but the human seat is created active right away."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db)
        agent = await _add_agent(db, user, name="Hawk", live=False)
        agent_id = agent.id

    r = await client.post(
        JOIN_URL,
        data={"play_as": "human", "agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Routed to bring the held AI online (the connect countdown or the connect setup).
    assert "/connect" in r.headers["location"] or "/me/connections" in r.headers["location"]

    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        agents = await _agents_by_id(db)
    assert len(players) == 2
    by_kind = {agents[p.agent_id].kind: p for p in players}
    assert by_kind[AgentKind.HUMAN].seat_reserved_until is None  # human active now
    assert by_kind[AgentKind.AI].seat_reserved_until is not None  # AI seat held


@pytest.mark.asyncio
async def test_practice_arena_does_not_autostart_when_agent_held(reset_db, client):
    """A practice arena starts on join only when every seat is live. A held AI seat
    (alongside the human) must NOT auto-start the game."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(
            db, max_players=10, match_kind=MatchKind.PRACTICE_ARENA.value
        )
        agent = await _add_agent(db, user, name="Hawk", live=False)
        agent_id = agent.id

    r = await client.post(
        JOIN_URL,
        data={"play_as": "human", "agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        match = (await db.execute(select(Match).where(Match.id == "M_0001"))).scalar_one()
    assert match.state == GameState.REGISTERING  # not auto-started


@pytest.mark.asyncio
async def test_human_seat_is_idempotent_alongside_agent(reset_db, client):
    """Joining human+agent when the user already holds a human seat does not add a
    second human seat — it just adds the agent seat."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db)
        agent = await _add_agent(db, user, name="Hawk", live=True)
        agent_id = agent.id

    # First: take a human seat only.
    r1 = await client.post(
        JOIN_URL,
        data={"play_as": "human"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r1.status_code == 303

    # Then: join again as human + agent. The human seat must not duplicate.
    r2 = await client.post(
        JOIN_URL,
        data={"play_as": "human", "agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r2.status_code == 303

    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        agents = await _agents_by_id(db)
    assert len(players) == 2
    assert _human_and_ai(players, agents) == (1, 1)


@pytest.mark.asyncio
async def test_join_screen_shows_both_independent_choices(reset_db, client):
    """The join screen offers 'Play as yourself' and 'Also send an AI agent' as two
    independently selectable boxes (keeping the per-agent AI picker cards)."""
    async with reset_db() as db:
        user = await make_user(db)
        await _make_match(db)
        await _add_agent(db, user, name="Hawk", live=True)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    assert "Play as yourself" in r.text
    assert "Also send an AI agent" in r.text
    assert "data-play-as-human" in r.text
    assert "data-play-as-agent" in r.text
    assert "Hawk" in r.text  # the agent card is still rendered
