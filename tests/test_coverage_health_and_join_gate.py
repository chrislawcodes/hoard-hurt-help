"""Tests for Part 1 (coverage-aware agent health) and Part 2 (SUM join-gate).

Covers:
  - Detached-but-covered agent reads as ready (not "needs connection")
  - is_join_blocked unit tests (DB-free)
  - active_matches_for_provider + live_provider_capacity query helpers
  - SUM join-gate: allowed when active < capacity, blocked when active >= capacity,
    tested at 0, 1, and 2 live connections
  - Provider with NO live connection → blocked / "no live connection" state
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.engine.connection_health import (
    active_matches_for_provider,
    is_join_blocked,
    live_provider_capacity,
    provider_is_covered,
    provider_loop_running,
)
from app.models import Base
from app.models.agent import AgentStatus
from app.models.connection import ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from tests.factories import make_agent, make_connection, make_user

LIVE_WINDOW = 90  # seconds — must match LIVE_WINDOW_SECONDS in connection_health.py


# Relative times are computed at call time (not at import) so they never drift
# outside LIVE_WINDOW as the overall suite runtime grows. See the flaky-failure
# note: a module-level `_RECENTLY` evaluated once at import would fall outside
# the 90s window if the suite took >~70s to reach these tests.
def _recently() -> datetime:
    """A check-in 20s ago — inside the live window."""
    return datetime.now(timezone.utc) - timedelta(seconds=20)


def _cold() -> datetime:
    """A check-in 10 minutes ago — outside the live window."""
    return datetime.now(timezone.utc) - timedelta(minutes=10)


# ---------------------------------------------------------------------------
# provider_loop_running — "is an AI actually playing", keyed off last_polled_at
# ---------------------------------------------------------------------------


async def test_loop_running_true_when_recently_polled(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 30)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = datetime.now(timezone.utc)
    conn.last_polled_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    await db_session.flush()
    assert await provider_loop_running(db_session, user.id, ConnectionProvider.CLAUDE)


async def test_loop_running_false_when_seen_but_never_polled(
    db_session: AsyncSession,
) -> None:
    """The core distinction: a connection SEEN just now (a sign-in handshake) but
    that never polled get_next_turn is NOT running the loop — even though
    provider_is_covered would call it 'live'."""
    user = await make_user(db_session, 31)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = datetime.now(timezone.utc)
    conn.last_seen_at = datetime.now(timezone.utc)  # seen → "covered" says live
    conn.last_polled_at = None  # but no play loop ever ran
    await db_session.flush()
    assert await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert not await provider_loop_running(db_session, user.id, ConnectionProvider.CLAUDE)


async def test_loop_running_false_when_poll_is_stale(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 32)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = datetime.now(timezone.utc)
    conn.last_polled_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.flush()
    assert not await provider_loop_running(db_session, user.id, ConnectionProvider.CLAUDE)


async def test_loop_running_false_when_paused(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 33)
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.CLAUDE, status=ConnectionStatus.PAUSED
    )
    conn.mcp_connected_at = datetime.now(timezone.utc)
    conn.last_polled_at = datetime.now(timezone.utc)
    await db_session.flush()
    assert not await provider_loop_running(db_session, user.id, ConnectionProvider.CLAUDE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _make_match(db: AsyncSession, match_id: str, *, state: GameState) -> Match:
    """Create a match with a valid scheduled_start (required by schema)."""
    now = _now()
    m = Match(
        id=match_id,
        name=f"Match {match_id}",
        game="hoard-hurt-help",
        state=state,
        scheduled_start=now - timedelta(hours=1),
        started_at=now - timedelta(hours=1) if state != GameState.SCHEDULED else None,
        per_turn_deadline_seconds=60,
    )
    db.add(m)
    await db.flush()
    return m


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
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Part 1 — is_join_blocked (DB-free unit tests)
# ---------------------------------------------------------------------------


def test_is_join_blocked_false_when_active_less_than_capacity() -> None:
    assert is_join_blocked(0, 3) is False
    assert is_join_blocked(2, 3) is False


def test_is_join_blocked_true_when_active_equals_capacity() -> None:
    assert is_join_blocked(3, 3) is True


def test_is_join_blocked_true_when_active_exceeds_capacity() -> None:
    assert is_join_blocked(5, 3) is True


def test_is_join_blocked_true_when_capacity_is_zero() -> None:
    """Zero capacity means no live connection covers the provider → always blocked."""
    assert is_join_blocked(0, 0) is True


# ---------------------------------------------------------------------------
# Part 1 — provider_is_covered
# ---------------------------------------------------------------------------


async def test_provider_is_covered_true_when_live_connection_exists(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 0)
    conn, _ = await make_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        status=ConnectionStatus.ACTIVE,
    )
    conn.last_seen_at = _recently()
    await db_session.flush()

    result = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is True


async def test_provider_is_covered_false_when_no_connection(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 1)
    result = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is False


async def test_provider_is_covered_false_when_connection_cold(
    db_session: AsyncSession,
) -> None:
    """A connection that last checked in 10 minutes ago is not live."""
    user = await make_user(db_session, 2)
    conn, _ = await make_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        status=ConnectionStatus.ACTIVE,
    )
    conn.last_seen_at = _cold()
    await db_session.flush()

    result = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is False


async def test_provider_is_covered_false_when_connection_paused(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 3)
    conn, _ = await make_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        status=ConnectionStatus.PAUSED,
    )
    conn.last_seen_at = _recently()
    await db_session.flush()

    result = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is False


async def test_provider_is_covered_false_when_provider_not_enabled(
    db_session: AsyncSession,
) -> None:
    """Connection exists and is live but the provider row is disabled."""
    user = await make_user(db_session, 4)
    conn, _ = await make_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        status=ConnectionStatus.ACTIVE,
    )
    conn.last_seen_at = _recently()
    # Disable the provider row that make_connection created.
    prow = (
        await db_session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(
                ConnectionProviderRow
            ).where(
                ConnectionProviderRow.connection_id == conn.id,
                ConnectionProviderRow.provider == ConnectionProvider.CLAUDE,
            )
        )
    ).scalar_one()
    prow.enabled = False
    await db_session.flush()

    result = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is False


# ---------------------------------------------------------------------------
# Part 1 — detached-but-covered agent reads as ready
# ---------------------------------------------------------------------------


async def test_detached_covered_agent_is_ready(db_session: AsyncSession) -> None:
    """An agent with a live connection covering its provider must resolve as
    covered — not 'needs connection'.
    """
    user = await make_user(db_session, 10)
    # Create a live connection covering CLAUDE.
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.CLAUDE, status=ConnectionStatus.ACTIVE
    )
    conn.last_seen_at = _recently()
    await db_session.flush()

    # Create an agent with provider=CLAUDE (derived from model).
    agent, _ = await make_agent(db_session, user, model="claude-haiku-4-5", status=AgentStatus.ACTIVE)
    # make_agent sets provider from model — should be CLAUDE.
    assert agent.provider == ConnectionProvider.CLAUDE

    result = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is True, "Agent's provider must be covered by the live connection"


# ---------------------------------------------------------------------------
# Part 2 — active_matches_for_provider + live_provider_capacity
# ---------------------------------------------------------------------------


async def test_active_matches_for_provider_counts_correctly(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 20)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    agent, version = await make_agent(db_session, user, connection=conn, model="claude-haiku-4-5")
    match = await _make_match(db_session, "M_ap1", state=GameState.ACTIVE)
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id if version else None,
        seat_name="A",
        model_self_report="claude-haiku-4-5",
    )
    db_session.add(player)
    await db_session.flush()

    count = await active_matches_for_provider(db_session, user.id, ConnectionProvider.CLAUDE)
    assert count == 1


async def test_active_matches_for_provider_zero_when_no_active_matches(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 21)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    await make_agent(db_session, user, connection=conn, model="claude-haiku-4-5")

    count = await active_matches_for_provider(db_session, user.id, ConnectionProvider.CLAUDE)
    assert count == 0


async def test_live_provider_capacity_zero_when_no_live_connections(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 30)
    # Connection exists but is cold.
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=3,
    )
    conn.last_seen_at = _cold()
    await db_session.flush()

    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.HERMES)
    assert cap == 0


async def test_live_provider_capacity_one_live_connection(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 31)
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=4,
    )
    conn.last_seen_at = _recently()
    await db_session.flush()

    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.HERMES)
    assert cap == 4


async def test_live_provider_capacity_two_live_connections_sums(
    db_session: AsyncSession,
) -> None:
    """Capacity is the SUM over all live connections that have the provider enabled."""
    user = await make_user(db_session, 32)
    conn1, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=3,
    )
    conn1.last_seen_at = _recently()
    conn2, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=5,
    )
    conn2.last_seen_at = _recently()
    await db_session.flush()

    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.HERMES)
    assert cap == 8  # 3 + 5


# ---------------------------------------------------------------------------
# Part 2 — SUM join-gate at 0, 1, and 2 live connections
# ---------------------------------------------------------------------------


async def test_join_gate_blocked_when_zero_live_connections(
    db_session: AsyncSession,
) -> None:
    """With no live connections, capacity is 0 → always blocked, even with 0 active matches."""
    user = await make_user(db_session, 40)
    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.CLAUDE)
    active = await active_matches_for_provider(db_session, user.id, ConnectionProvider.CLAUDE)
    assert is_join_blocked(active, cap) is True


async def test_join_gate_allowed_under_capacity_one_connection(
    db_session: AsyncSession,
) -> None:
    """One live connection with capacity 2, zero active matches → allowed."""
    user = await make_user(db_session, 41)
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=2,
    )
    conn.last_seen_at = _recently()
    await db_session.flush()

    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.HERMES)
    active = await active_matches_for_provider(db_session, user.id, ConnectionProvider.HERMES)
    assert cap == 2
    assert active == 0
    assert is_join_blocked(active, cap) is False


async def test_join_gate_blocked_at_capacity_one_connection(
    db_session: AsyncSession,
) -> None:
    """One live connection with capacity 1, one active match → blocked."""
    user = await make_user(db_session, 42)
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=1,
    )
    conn.last_seen_at = _recently()
    await db_session.flush()

    agent, version = await make_agent(
        db_session, user, connection=conn, model="claude-haiku-4-5"
    )
    match = await _make_match(db_session, "M_gate1", state=GameState.ACTIVE)
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id if version else None,
        seat_name="A",
        model_self_report="claude-haiku-4-5",
    )
    db_session.add(player)
    await db_session.flush()

    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.HERMES)
    active = await active_matches_for_provider(db_session, user.id, ConnectionProvider.HERMES)
    assert cap == 1
    assert active == 1
    assert is_join_blocked(active, cap) is True


async def test_join_gate_scales_with_two_live_connections(
    db_session: AsyncSession,
) -> None:
    """Two live connections with capacity 2 each → total 4.
    One active match → allowed; four active matches → blocked.
    """
    user = await make_user(db_session, 43)
    conn1, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=2,
    )
    conn1.last_seen_at = _recently()
    conn2, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.HERMES, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=2,
    )
    conn2.last_seen_at = _recently()
    await db_session.flush()

    # Create 3 active matches on a single agent.
    agent, version = await make_agent(
        db_session, user, connection=conn1, model="claude-haiku-4-5"
    )
    for i in range(3):
        match = await _make_match(db_session, f"M_scale_{i}", state=GameState.ACTIVE)
        player = Player(
            match_id=match.id,
            user_id=user.id,
            agent_id=agent.id,
            agent_version_id=version.id if version else None,
            seat_name=f"A{i}",
            model_self_report="claude-haiku-4-5",
        )
        db_session.add(player)
    await db_session.flush()

    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.HERMES)
    active = await active_matches_for_provider(db_session, user.id, ConnectionProvider.HERMES)
    assert cap == 4      # 2 + 2
    assert active == 3
    assert is_join_blocked(active, cap) is False   # 3 < 4 → allowed

    # Now add a fourth match → exactly at capacity → blocked.
    match4 = await _make_match(db_session, "M_scale_4", state=GameState.ACTIVE)
    player4 = Player(
        match_id=match4.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id if version else None,
        seat_name="A4",
        model_self_report="claude-haiku-4-5",
    )
    db_session.add(player4)
    await db_session.flush()

    active2 = await active_matches_for_provider(db_session, user.id, ConnectionProvider.HERMES)
    assert active2 == 4
    assert is_join_blocked(active2, cap) is True   # 4 >= 4 → blocked


async def test_join_gate_blocked_no_live_connection_covers_provider(
    db_session: AsyncSession,
) -> None:
    """Provider covered by NO live connection → capacity 0 → always blocked,
    and provider_is_covered returns False."""
    user = await make_user(db_session, 50)
    # Create a cold connection for CLAUDE only.
    conn, _ = await make_connection(
        db_session, user, provider=ConnectionProvider.CLAUDE, status=ConnectionStatus.ACTIVE,
        max_concurrent_games=3,
    )
    conn.last_seen_at = _cold()
    await db_session.flush()

    covered = await provider_is_covered(db_session, user.id, ConnectionProvider.CLAUDE)
    cap = await live_provider_capacity(db_session, user.id, ConnectionProvider.CLAUDE)
    active = await active_matches_for_provider(db_session, user.id, ConnectionProvider.CLAUDE)

    assert covered is False
    assert cap == 0
    assert is_join_blocked(active, cap) is True
