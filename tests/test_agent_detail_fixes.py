"""Tests for agent detail page fixes: matches section, ready-to-play card,
stall/last-connected diagnostics, and onboarding status narration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import make_engine
from app.engine.agent_onboarding import AgentOnboardingState, compute_agent_onboarding_state
from app.engine.connection_health import ConnectionHealth
from app.models import Base
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import TurnSubmission
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
from tests.factories import (
    add_submission,
    make_agent,
    make_connection,
    make_match,
    make_turn,
    make_user,
    seat_prebuilt_player,
)
from tests.conftest import signed_in_cookies as _cookies

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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


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
    """Seed a connection, deriving `mcp_connected_at` the way a real MCP sign-in
    would: set for the providers that only connect via MCP, left null otherwise.
    `tests.factories.make_connection` has no opinion on that derivation, so it
    stays a thin wrapper here rather than a plain re-export."""
    mcp_connected_at = (
        (first_connected_at or last_seen_at)
        if provider in {ConnectionProvider.CLAUDE, ConnectionProvider.OPENAI, ConnectionProvider.GEMINI}
        else None
    )
    connection, _key = await make_connection(
        db,
        user,
        provider=provider,
        status=status,
        max_concurrent_games=max_concurrent_games,
        last_seen_at=last_seen_at,
        first_connected_at=first_connected_at,
        mcp_connected_at=mcp_connected_at,
    )
    return connection


async def _make_match(
    db: AsyncSession,
    match_id: str,
    *,
    state: GameState,
) -> Match:
    """Seed a match that "already happened" (started an hour before the file's
    frozen NOW), unlike `tests.factories.make_match`'s default of a not-yet-
    started REGISTERING match — this file's fixtures are all mid-game/finished."""
    started = NOW - timedelta(hours=1)
    return await make_match(
        db,
        match_id,
        state=state,
        scheduled_start=started,
        started_at=started if state != GameState.SCHEDULED else None,
        completed_at=NOW if state == GameState.COMPLETED else None,
    )


# ---------------------------------------------------------------------------
# Fix 1: Matches section
# ---------------------------------------------------------------------------


async def test_load_agent_matches_returns_active_upcoming_done_ordering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await make_user(db, i=0, handle="agent0")
        conn = await _make_connection(db, user)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")

        active_match = await _make_match(db, "M_active", state=GameState.ACTIVE)
        upcoming_match = await _make_match(db, "M_upcoming", state=GameState.SCHEDULED)
        done_match = await _make_match(db, "M_done", state=GameState.COMPLETED)

        await seat_prebuilt_player(db, match=active_match, user=user, agent=agent, version=version, seat_name="A")
        await seat_prebuilt_player(db, match=upcoming_match, user=user, agent=agent, version=version, seat_name="A")
        await seat_prebuilt_player(db, match=done_match, user=user, agent=agent, version=version, seat_name="A")
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


async def test_load_agent_matches_caps_done_at_10(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await make_user(db, i=1, handle="agent1")
        conn = await _make_connection(db, user)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")

        for i in range(15):
            m = await _make_match(db, f"M_done_{i}", state=GameState.COMPLETED)
            await seat_prebuilt_player(db, match=m, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)

    # Should cap done matches at 10
    assert len(entries) == 10
    assert all(e.state == GameState.COMPLETED for e in entries)


async def test_agent_detail_shows_matches_section(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await make_user(db, i=2, handle="agent2")
        conn = await _make_connection(db, user)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        active_match = await _make_match(db, "M_show", state=GameState.ACTIVE)
        await seat_prebuilt_player(db, match=active_match, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Match M_show" in resp.text
    assert "Watch →" in resp.text
    # Active match should show View strategy link (not Manage)
    assert "View strategy →" in resp.text
    # Active match should NOT show Leave button
    assert "Leave" not in resp.text


async def test_agent_detail_matches_shows_leave_for_pre_game(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await make_user(db, i=3, handle="agent3")
        conn = await _make_connection(db, user)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        pre_match = await _make_match(db, "M_pre", state=GameState.SCHEDULED)
        await seat_prebuilt_player(db, match=pre_match, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Match M_pre" in resp.text
    assert "Manage →" in resp.text
    assert "Leave" in resp.text


async def test_agent_detail_shows_no_matches_empty_state(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await make_user(db, i=4, handle="agent4")
        conn = await _make_connection(db, user)
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
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


async def test_agent_detail_shows_ready_to_play_card_when_warm(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=5, handle="agent5")
        conn = await _make_connection(db, user, last_seen_at=recently)
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Ready to play" in resp.text
    assert "Find a match →" in resp.text


async def test_agent_detail_hides_ready_to_play_when_at_capacity(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Agent in an active match on a saturated connection: shows match card, not ready-to-play.

    When the agent is actively in a match (onboarding state IN_GAME_NO_MOVE),
    the match card takes priority. The 'At capacity' card only appears for the
    idle-but-connected state (CONNECTED_NO_GAME) when join_blocked is True.
    """
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=6, handle="agent6")
        conn = await _make_connection(db, user, last_seen_at=recently, max_concurrent_games=1)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        m = await _make_match(db, "M_cap", state=GameState.ACTIVE)
        await seat_prebuilt_player(db, match=m, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    # Agent is in an active match → shows match card, not at-capacity or ready-to-play
    assert "Ready to play" not in resp.text
    assert "In a match" in resp.text or "At capacity" not in resp.text


async def test_agent_detail_hides_ready_to_play_when_paused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=7, handle="agent7")
        conn = await _make_connection(
            db, user, status=ConnectionStatus.PAUSED, last_seen_at=recently
        )
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Ready to play" not in resp.text


# ---------------------------------------------------------------------------
# Fix 3: Last-connected / stall diagnostics
# ---------------------------------------------------------------------------


async def test_agent_detail_shows_no_live_connection_when_never_connected(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When no live connection covers the provider, the detail page shows
    'No live connection runs <provider>' — the coverage-based message."""
    async with session_factory() as db:
        user = await make_user(db, i=8, handle="agent8")
        conn = await _make_connection(db, user)  # no last_seen_at → not live
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "No live AI connection yet" in resp.text


async def test_agent_detail_shows_no_live_connection_when_cold(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When the only connection is cold (last_seen_at well past the live window),
    the coverage check fails and the detail page shows the 'No live connection' card."""
    async with session_factory() as db:
        user = await make_user(db, i=9, handle="agent9")
        conn = await _make_connection(db, user, last_seen_at=COLD)
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "No live AI connection yet" in resp.text


async def test_agent_detail_no_reconnect_card_when_live(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Live agent should not show reconnect / runner-down warnings."""
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=12, handle="agentC")
        conn = await _make_connection(db, user, last_seen_at=recently)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        m = await _make_match(db, "M_live2", state=GameState.ACTIVE)
        await seat_prebuilt_player(db, match=m, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Runner isn't running" not in resp.text
    assert "hasn't connected yet" not in resp.text
    assert "Needs connection" not in resp.text


# ---------------------------------------------------------------------------
# Fix 4: Onboarding status narration (states 1–5)
# ---------------------------------------------------------------------------


async def _make_turn_submission(
    db: AsyncSession,
    *,
    match: Match,
    player: Player,
    turn_no: int = 1,
    was_defaulted: bool = False,
) -> TurnSubmission:
    """Create a turn and a submission for it."""
    turn = await make_turn(
        db,
        match.id,
        turn=turn_no,
        phase="talk",
        resolved=False,
        opened_at=NOW,
    )
    return await add_submission(
        db, turn, player, action="HOARD", was_defaulted=was_defaulted, submitted_at=NOW
    )


async def test_onboarding_state_waiting_never_connected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """State 1: no first_connected_at and no matches → WAITING."""
    async with session_factory() as db:
        user = await make_user(db, i=13, handle="ob0")
        conn = await _make_connection(db, user)
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

        status = await compute_agent_onboarding_state(
            db,
            agent_id=agent.id,
            first_connected_at=None,
            matches=[],
        )

    assert status.state == AgentOnboardingState.WAITING
    assert status.match_id is None


async def test_onboarding_state_connected_no_game(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """State 2: connected but no matches → CONNECTED_NO_GAME."""
    async with session_factory() as db:
        user = await make_user(db, i=14, handle="ob1")
        conn = await _make_connection(db, user, first_connected_at=NOW)
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

        status = await compute_agent_onboarding_state(
            db,
            agent_id=agent.id,
            first_connected_at=NOW,
            matches=[],
        )

    assert status.state == AgentOnboardingState.CONNECTED_NO_GAME
    assert status.match_id is None


async def test_onboarding_state_connected_pregame(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """State 3: connected, in a pre-game match → CONNECTED_PREGAME."""
    async with session_factory() as db:
        user = await make_user(db, i=15, handle="ob2")
        conn = await _make_connection(db, user, first_connected_at=NOW)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        match = await _make_match(db, "M_pregame", state=GameState.SCHEDULED)
        await seat_prebuilt_player(db, match=match, user=user, agent=agent, version=version, seat_name="A")
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)
        status = await compute_agent_onboarding_state(
            db,
            agent_id=agent.id,
            first_connected_at=NOW,
            matches=list(entries),
        )

    assert status.state == AgentOnboardingState.CONNECTED_PREGAME
    assert status.match_id == "M_pregame"
    assert status.match_name == "Match M_pregame"


async def test_onboarding_state_in_game_no_move(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """State 4: connected, in active match, no real move yet → IN_GAME_NO_MOVE."""
    async with session_factory() as db:
        user = await make_user(db, i=16, handle="ob3")
        conn = await _make_connection(db, user, first_connected_at=NOW)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        match = await _make_match(db, "M_active_nomove", state=GameState.ACTIVE)
        player = await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="A"
        )
        # Add a defaulted submission — should NOT count as "has moved"
        await _make_turn_submission(db, match=match, player=player, was_defaulted=True)
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)
        status = await compute_agent_onboarding_state(
            db,
            agent_id=agent.id,
            first_connected_at=NOW,
            matches=list(entries),
        )

    assert status.state == AgentOnboardingState.IN_GAME_NO_MOVE
    assert status.match_id == "M_active_nomove"


async def test_onboarding_state_playing_first_real_move(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """State 5: has a real (non-defaulted) submission → PLAYING with watch link."""
    async with session_factory() as db:
        user = await make_user(db, i=17, handle="ob4")
        conn = await _make_connection(db, user, first_connected_at=NOW)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        match = await _make_match(db, "M_playing", state=GameState.ACTIVE)
        player = await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="A"
        )
        await _make_turn_submission(db, match=match, player=player, was_defaulted=False)
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)
        status = await compute_agent_onboarding_state(
            db,
            agent_id=agent.id,
            first_connected_at=NOW,
            matches=list(entries),
        )

    assert status.state == AgentOnboardingState.PLAYING
    # Active match should be surfaced so the "Watch it play" link works
    assert status.match_id == "M_playing"
    assert status.game_type == "hoard-hurt-help"


async def test_onboarding_state_playing_even_when_cold(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """State 5: PLAYING resolves correctly even when the runner is currently cold."""
    async with session_factory() as db:
        user = await make_user(db, i=18, handle="ob5")
        # No first_connected_at — this is a legacy agent that pre-dates first_connected_at
        conn = await _make_connection(db, user)
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        match = await _make_match(db, "M_cold_play", state=GameState.ACTIVE)
        player = await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="A"
        )
        # Real move exists — has_moved should dominate
        await _make_turn_submission(db, match=match, player=player, was_defaulted=False)
        await db.commit()

        entries = await _load_agent_matches(db, agent.id)
        status = await compute_agent_onboarding_state(
            db,
            agent_id=agent.id,
            first_connected_at=None,  # cold / legacy — no first_connected_at
            matches=list(entries),
        )

    assert status.state == AgentOnboardingState.PLAYING


# ---------------------------------------------------------------------------
# Fix 4b: Polled /status endpoint renders the right card
# ---------------------------------------------------------------------------


async def test_status_fragment_shows_ready_to_play_for_connected_idle_agent(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Connected idle agent: /status fragment shows 'Ready to play' card."""
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=19, handle="ob6")
        conn = await _make_connection(
            db, user, last_seen_at=recently, first_connected_at=recently
        )
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}/status", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "Ready to play" in resp.text
    assert "Find a match →" in resp.text


async def test_status_fragment_hides_playing_banner(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A played agent no longer renders the old playing banner in /status."""
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=20, handle="ob7")
        conn = await _make_connection(
            db, user, last_seen_at=recently, first_connected_at=recently
        )
        agent, version = await make_agent(db, user, connection=conn, name="Alpha")
        match = await _make_match(db, "M_frag_play", state=GameState.ACTIVE)
        player = await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="A"
        )
        await _make_turn_submission(db, match=match, player=player, was_defaulted=False)
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}/status", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert resp.text.strip() == ""


async def test_status_fragment_shows_at_capacity_card(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Connection at capacity with a second agent idle: /status shows 'At capacity'.

    The at-capacity card applies to connected_no_game (state 2) when join_blocked
    is True. We use two agents on a max_concurrent_games=1 connection: agent1 is
    in the active match (which saturates the connection), agent2 is idle but the
    connection is full, so agent2's /status shows At capacity.
    """
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=21, handle="ob8")
        conn = await _make_connection(
            db, user, last_seen_at=recently, first_connected_at=recently, max_concurrent_games=1
        )
        agent1, version1 = await make_agent(db, user, connection=conn, name="Cap1")
        agent2, _ = await make_agent(db, user, connection=conn, name="Cap2")
        match = await _make_match(db, "M_cap2", state=GameState.ACTIVE)
        await seat_prebuilt_player(
            db, match=match, user=user, agent=agent1, version=version1, seat_name="A"
        )
        await db.commit()

    # agent2 is connected (first_connected_at is set) and idle → connected_no_game,
    # but the connection is at capacity → At capacity card.
    resp = await client.get(f"/me/agents/{agent2.id}/status", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert "At capacity" in resp.text
    assert "Ready to play" not in resp.text


async def test_detail_page_shows_onboarding_card_inline(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """detail.html inlines the onboarding card on first paint (not just htmx-polled)."""
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=22, handle="ob9")
        conn = await _make_connection(
            db, user, last_seen_at=recently, first_connected_at=recently
        )
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    # The onboarding slot container with htmx polling should be present
    assert "onboarding-status" in resp.text
    # And the initial card is rendered inline (state 2: ready to play)
    assert "Ready to play" in resp.text


async def test_detail_name_field_autosaves_on_change(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The agent rename field submits itself when the value changes."""
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await make_user(db, i=23, handle="ob10")
        conn = await _make_connection(
            db, user, last_seen_at=recently, first_connected_at=recently
        )
        agent, _ = await make_agent(db, user, connection=conn, name="Alpha")
        await db.commit()

    resp = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert 'onchange="this.form.requestSubmit()"' in resp.text
    assert 'class="secondary">Rename</button>' not in resp.text
