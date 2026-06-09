"""Tests for agent detail page fixes: matches section, ready-to-play card,
and stall/last-connected diagnostics."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import make_engine
from app.engine.connection_health import ConnectionHealth
from app.engine.tokens import bot_key_lookup, generate_connection_key
from app.models import Base
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User
from app.routes.agents_lifecycle import router as agents_lifecycle_router
from app.routes.agents_setup import (
    _is_ready_to_play,
    _load_agent_matches,
    router as agents_setup_router,
)
from app.routes.agents_status import router as agents_status_router
from app.routes.connections_credentials import router as connections_credentials_router
from app.routes.connections_lifecycle import router as connections_lifecycle_router
from app.routes.connections_setup import router as connections_setup_router

NOW = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
# COLD is used for DB fixtures: well past the health engine's 90s live window regardless
# of when tests actually run (connection was last seen long in the past).
COLD = NOW - timedelta(minutes=10)
# PAST_RECENT is a frozen time used only for unit-test data payloads
# (ConnectionHealthStatus). HTTP endpoint tests use datetime.now() - timedelta(seconds=20)
# so they pass the live-window check at actual runtime.
PAST_RECENT = NOW - timedelta(seconds=20)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def app(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.db.engine", engine)
    test_app = FastAPI()
    test_app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=False,
        session_cookie="hhh_session",
    )
    test_app.include_router(agents_setup_router, prefix="/me/agents")
    test_app.include_router(agents_status_router, prefix="/me/agents")
    test_app.include_router(agents_lifecycle_router, prefix="/me/agents")
    test_app.include_router(connections_setup_router, prefix="/me/connections")
    test_app.include_router(connections_credentials_router, prefix="/me/connections")
    test_app.include_router(connections_lifecycle_router, prefix="/me/connections")
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(
        json.dumps({"user_id": user_id, "next_after_login": None}).encode()
    ).decode()
    return {"hhh_session": signer.sign(payload).decode()}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession, *, handle: str = "tester", i: int = 0) -> User:
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@example.com",
        handle=handle,
        handle_key=handle,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_connection(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    max_concurrent_games: int = 3,
    last_seen_at: datetime | None = None,
    first_connected_at: datetime | None = None,
) -> Connection:
    plain_key = generate_connection_key()
    conn = Connection(
        user_id=user.id,
        provider=provider,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=plain_key[-4:],
        status=status,
        max_concurrent_games=max_concurrent_games,
        last_seen_at=last_seen_at,
        first_connected_at=first_connected_at,
    )
    db.add(conn)
    await db.flush()
    return conn


async def _make_agent(
    db: AsyncSession,
    user: User,
    *,
    connection: Connection | None,
    name: str = "Alpha",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> tuple[Agent, AgentVersion]:
    agent = Agent(
        user_id=user.id,
        connection_id=connection.id if connection else None,
        kind=AgentKind.AI,
        name=name,
        game="hoard-hurt-help",
        status=status,
    )
    db.add(agent)
    await db.flush()
    version = AgentVersion(
        agent_id=agent.id,
        version_no=1,
        model="claude-haiku-4-5",
        strategy_text="Play to win.",
    )
    db.add(version)
    await db.flush()
    agent.current_version_id = version.id
    await db.flush()
    return agent, version


async def _make_match(
    db: AsyncSession,
    match_id: str,
    *,
    state: GameState,
) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game="hoard-hurt-help",
        state=state,
        scheduled_start=NOW - timedelta(hours=1),
        started_at=NOW - timedelta(hours=1) if state != GameState.SCHEDULED else None,
        completed_at=NOW if state == GameState.COMPLETED else None,
        per_turn_deadline_seconds=60,
    )
    db.add(match)
    await db.flush()
    return match


async def _seat_player(
    db: AsyncSession,
    *,
    match: Match,
    user: User,
    agent: Agent,
    version: AgentVersion,
    seat_name: str,
    total_score: int = 0,
    round_score: int = 0,
) -> Player:
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        model_self_report=version.model,
        total_round_score=total_score,
        current_round_score=round_score,
    )
    db.add(player)
    await db.flush()
    return player


# ---------------------------------------------------------------------------
# Fix 1: Matches section
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_agent_matches_returns_active_upcoming_done_ordering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent0", i=0)
        conn = await _make_connection(db, user)
        agent, version = await _make_agent(db, user, connection=conn)

        active_match = await _make_match(db, "M_active", state=GameState.ACTIVE)
        upcoming_match = await _make_match(db, "M_upcoming", state=GameState.SCHEDULED)
        done_match = await _make_match(db, "M_done", state=GameState.COMPLETED)

        await _seat_player(db, match=active_match, user=user, agent=agent, version=version, seat_name="A")
        await _seat_player(db, match=upcoming_match, user=user, agent=agent, version=version, seat_name="A")
        await _seat_player(db, match=done_match, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)

    assert len(entries) == 3
    # Active comes first
    assert entries[0].match_id == "M_active"
    assert entries[0].state == GameState.ACTIVE
    assert entries[0].pre_game is False
    # Upcoming second
    assert entries[1].match_id == "M_upcoming"
    assert entries[1].pre_game is True
    # Done last
    assert entries[2].match_id == "M_done"
    assert entries[2].pre_game is False


@pytest.mark.asyncio
async def test_load_agent_matches_caps_done_at_10(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent1", i=1)
        conn = await _make_connection(db, user)
        agent, version = await _make_agent(db, user, connection=conn)

        for i in range(15):
            m = await _make_match(db, f"M_done_{i}", state=GameState.COMPLETED)
            await _seat_player(db, match=m, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)

    # Should cap done matches at 10
    assert len(entries) == 10
    assert all(e.state == GameState.COMPLETED for e in entries)


@pytest.mark.asyncio
async def test_agent_detail_shows_matches_section(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent2", i=2)
        conn = await _make_connection(db, user)
        agent, version = await _make_agent(db, user, connection=conn)
        active_match = await _make_match(db, "M_show", state=GameState.ACTIVE)
        await _seat_player(db, match=active_match, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Match M_show" in resp.text
    assert "Watch →" in resp.text
    # Active match should show View strategy link (not Manage)
    assert "View strategy →" in resp.text
    # Active match should NOT show Leave button
    assert "Leave" not in resp.text


@pytest.mark.asyncio
async def test_agent_detail_matches_shows_leave_for_pre_game(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent3", i=3)
        conn = await _make_connection(db, user)
        agent, version = await _make_agent(db, user, connection=conn)
        pre_match = await _make_match(db, "M_pre", state=GameState.SCHEDULED)
        await _seat_player(db, match=pre_match, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Match M_pre" in resp.text
    assert "Manage →" in resp.text
    assert "Leave" in resp.text


@pytest.mark.asyncio
async def test_agent_detail_shows_no_matches_empty_state(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent4", i=4)
        conn = await _make_connection(db, user)
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "isn't in any matches yet" in resp.text


# ---------------------------------------------------------------------------
# Fix 2: Ready-to-play card
# ---------------------------------------------------------------------------


def test_is_ready_to_play_true_when_live() -> None:
    from app.engine.connection_health import ConnectionHealthStatus

    health = ConnectionHealthStatus(
        state=ConnectionHealth.LIVE,
        label="Live",
        badge_class="badge-ok",
        pulse=True,
        needs_reconnect=False,
        never_connected=False,
        last_connected_at=PAST_RECENT,
        last_connected_human="20s ago",
    )
    ctx: dict[str, object] = {"health": health, "join_blocked": False}
    assert _is_ready_to_play(ctx) is True


def test_is_ready_to_play_true_when_ready() -> None:
    from app.engine.connection_health import ConnectionHealthStatus

    health = ConnectionHealthStatus(
        state=ConnectionHealth.READY,
        label="Ready",
        badge_class="badge-ok",
        pulse=False,
        needs_reconnect=False,
        never_connected=False,
        last_connected_at=PAST_RECENT,
        last_connected_human="20s ago",
    )
    ctx: dict[str, object] = {"health": health, "join_blocked": False}
    assert _is_ready_to_play(ctx) is True


def test_is_ready_to_play_false_when_disconnected() -> None:
    from app.engine.connection_health import ConnectionHealthStatus

    health = ConnectionHealthStatus(
        state=ConnectionHealth.DISCONNECTED,
        label="Disconnected",
        badge_class="badge-alert",
        pulse=False,
        needs_reconnect=True,
        never_connected=True,
        last_connected_at=None,
        last_connected_human=None,
    )
    ctx: dict[str, object] = {"health": health, "join_blocked": False}
    assert _is_ready_to_play(ctx) is False


def test_is_ready_to_play_false_when_paused() -> None:
    ctx: dict[str, object] = {
        "health": {
            "state": ConnectionHealth.PAUSED,
            "label": "Paused",
            "badge_class": "badge-done",
            "pulse": False,
            "needs_reconnect": False,
            "never_connected": False,
            "last_connected_at": None,
            "last_connected_human": None,
        },
        "join_blocked": False,
    }
    assert _is_ready_to_play(ctx) is False


def test_is_ready_to_play_false_when_join_blocked() -> None:
    from app.engine.connection_health import ConnectionHealthStatus

    health = ConnectionHealthStatus(
        state=ConnectionHealth.READY,
        label="Ready",
        badge_class="badge-ok",
        pulse=False,
        needs_reconnect=False,
        never_connected=False,
        last_connected_at=PAST_RECENT,
        last_connected_human="20s ago",
    )
    ctx: dict[str, object] = {"health": health, "join_blocked": True}
    assert _is_ready_to_play(ctx) is False


@pytest.mark.asyncio
async def test_agent_detail_shows_ready_to_play_card_when_warm(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await _make_user(db, handle="agent5", i=5)
        conn = await _make_connection(db, user, last_seen_at=recently)
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Ready to play" in resp.text
    assert "Find a match →" in resp.text


@pytest.mark.asyncio
async def test_agent_detail_hides_ready_to_play_when_at_capacity(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await _make_user(db, handle="agent6", i=6)
        conn = await _make_connection(db, user, last_seen_at=recently, max_concurrent_games=1)
        agent, version = await _make_agent(db, user, connection=conn)
        m = await _make_match(db, "M_cap", state=GameState.ACTIVE)
        await _seat_player(db, match=m, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "At capacity" in resp.text
    assert "Ready to play" not in resp.text


@pytest.mark.asyncio
async def test_agent_detail_hides_ready_to_play_when_paused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await _make_user(db, handle="agent7", i=7)
        conn = await _make_connection(
            db, user, status=ConnectionStatus.PAUSED, last_seen_at=recently
        )
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Ready to play" not in resp.text


# ---------------------------------------------------------------------------
# Fix 3: Last-connected / stall diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_detail_shows_never_connected_in_status(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When runner has never connected, the status fragment says 'never connected'."""
    async with session_factory() as db:
        user = await _make_user(db, handle="agent8", i=8)
        conn = await _make_connection(db, user)  # no last_seen_at
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "never connected" in resp.text


@pytest.mark.asyncio
async def test_agent_detail_shows_last_connected_when_cold(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When runner has been seen but is cold, last-connected time appears."""
    async with session_factory() as db:
        user = await _make_user(db, handle="agent9", i=9)
        conn = await _make_connection(db, user, last_seen_at=COLD)
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "last connected" in resp.text


@pytest.mark.asyncio
async def test_agent_detail_stall_diagnostics_never_connected_case(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Agent with connection that has never seen the runner shows 'hasn't connected yet'."""
    async with session_factory() as db:
        user = await _make_user(db, handle="agentA", i=10)
        conn = await _make_connection(db, user)  # no last_seen_at — never connected
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "hasn't connected yet" in resp.text or "never connected" in resp.text


@pytest.mark.asyncio
async def test_agent_detail_stall_diagnostics_runner_down_case(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Agent with cold connection shows 'Runner isn't running'."""
    async with session_factory() as db:
        user = await _make_user(db, handle="agentB", i=11)
        conn = await _make_connection(db, user, last_seen_at=COLD)
        agent, _ = await _make_agent(db, user, connection=conn)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Runner isn't running" in resp.text


@pytest.mark.asyncio
async def test_agent_detail_no_reconnect_card_when_live(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Live agent should not show reconnect / runner-down warnings."""
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await _make_user(db, handle="agentC", i=12)
        conn = await _make_connection(db, user, last_seen_at=recently)
        agent, version = await _make_agent(db, user, connection=conn)
        m = await _make_match(db, "M_live2", state=GameState.ACTIVE)
        await _seat_player(db, match=m, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Runner isn't running" not in resp.text
    assert "hasn't connected yet" not in resp.text
    assert "Needs connection" not in resp.text
