"""Tests for `resolve_play_setup_state` — the play-setup gate resolver (Slice 2).

Covers each stage transition, the `require` threshold clamp, the multi-agent
most-ready reduction, the exclusion rules (`kind=bot` / `archived_at` /
`provider IS NULL`), `next_url` correctness (incl. `?next=` threading for a join
target), the AD-4 query bound (a single-provider ready user issues a small,
bounded number of readiness queries — no 3·K blow-up), and the nav ⚠ change
(`compute_nav_cta` "Play now" now keys on current MCP setup, not "connected once").
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Connection as SAConnection
from sqlalchemy import event
from sqlalchemy.engine.interfaces import ExecutionContext
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.models import Base
from app.models.agent import AgentKind
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match
from app.routes.nav_context import (
    PlaySetupStage,
    compute_nav_cta,
    resolve_play_setup_state,
)
from tests.factories import make_agent, make_connection, make_user


def _recently() -> datetime:
    """A timestamp 20s ago — inside the live / loop-running windows."""
    return datetime.now(timezone.utc) - timedelta(seconds=20)


def _cold() -> datetime:
    """A timestamp 10 minutes ago — outside every liveness window."""
    return datetime.now(timezone.utc) - timedelta(minutes=10)


def _stale_100_days() -> datetime:
    """A timestamp 100 days ago — past the 90-day MCP validity window."""
    return datetime.now(timezone.utc) - timedelta(days=100)


# ---------------------------------------------------------------------------
# Fixtures (local engine/session, matching test_provider_readiness.py)
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


async def _make_registering_match(db: AsyncSession, match_id: str) -> Match:
    """A REGISTERING match with the required scheduled_start set."""
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game="hoard-hurt-help",
        state=GameState.REGISTERING,
        per_turn_deadline_seconds=60,
        scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(match)
    await db.flush()
    return match


async def _mcp_agent(
    db: AsyncSession,
    user,
    *,
    provider: ConnectionProvider,
    mcp_connected_at: datetime | None,
    last_seen_at: datetime | None = None,
    last_polled_at: datetime | None = None,
    name: str | None = None,
):
    """Create a connection (with the given liveness) plus a matching AI agent."""
    conn, _ = await make_connection(db, user, provider=provider)
    conn.mcp_connected_at = mcp_connected_at
    conn.last_seen_at = last_seen_at
    conn.last_polled_at = last_polled_at
    await db.flush()
    agent, _ = await make_agent(db, user, connection=conn, name=name)
    return agent, conn


# ---------------------------------------------------------------------------
# Stage transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_user_is_not_signed_in(db_session: AsyncSession) -> None:
    state = await resolve_play_setup_state(db_session, None)
    assert state.stage is PlaySetupStage.NOT_SIGNED_IN
    assert state.next_url == "/auth/google/login"


@pytest.mark.asyncio
async def test_user_without_handle_needs_handle(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    user.handle = None
    await db_session.flush()
    state = await resolve_play_setup_state(db_session, user)
    assert state.stage is PlaySetupStage.NEEDS_HANDLE
    assert state.next_url == "/me/handle"


@pytest.mark.asyncio
async def test_handle_no_agent_needs_agent(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 1)
    state = await resolve_play_setup_state(db_session, user)
    assert state.stage is PlaySetupStage.NEEDS_AGENT
    assert state.next_url == "/me/agents/new"


@pytest.mark.asyncio
async def test_agent_no_mcp_connection_needs_mcp_connection(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 2)
    await _mcp_agent(db_session, user, provider=ConnectionProvider.CLAUDE, mcp_connected_at=None)
    state = await resolve_play_setup_state(db_session, user)
    assert state.stage is PlaySetupStage.NEEDS_MCP_CONNECTION
    # The connect page is no longer provider-scoped — any connection plays any agent.
    assert state.next_url == "/me/connections"


@pytest.mark.asyncio
async def test_connected_not_live_with_nav_require_is_ready(
    db_session: AsyncSession,
) -> None:
    # CONNECTED_NOT_LIVE → first-unmet = NEEDS_LIVE (4); nav require=3 → READY.
    user = await make_user(db_session, 3)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_recently(),
        last_seen_at=_cold(),
    )
    state = await resolve_play_setup_state(
        db_session, user, require=PlaySetupStage.NEEDS_MCP_CONNECTION
    )
    assert state.stage is PlaySetupStage.READY
    assert state.next_url == "/games/hoard-hurt-help#lobby-upcoming"


@pytest.mark.asyncio
async def test_connected_not_live_with_require_ready_is_needs_live(
    db_session: AsyncSession,
) -> None:
    # Same agent, but require=READY → NEEDS_LIVE stays unmet.
    user = await make_user(db_session, 4)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_recently(),
        last_seen_at=_cold(),
    )
    state = await resolve_play_setup_state(
        db_session, user, require=PlaySetupStage.READY
    )
    assert state.stage is PlaySetupStage.NEEDS_LIVE


@pytest.mark.asyncio
async def test_live_agent_is_ready_at_any_require(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 5)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_recently(),
        last_seen_at=_recently(),
        last_polled_at=_recently(),
    )
    for require in (PlaySetupStage.NEEDS_MCP_CONNECTION, PlaySetupStage.READY):
        state = await resolve_play_setup_state(db_session, user, require=require)
        assert state.stage is PlaySetupStage.READY, require


# ---------------------------------------------------------------------------
# Multi-agent most-ready reduction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_agent_most_ready_wins(db_session: AsyncSession) -> None:
    # One NO_MCP_CONNECTION agent (claude) + one CONNECTED_NOT_LIVE agent (gemini).
    # With nav require, the connected provider clears the bar → READY.
    user = await make_user(db_session, 6)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=None,
        name="cold-claude",
    )
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.GEMINI,
        mcp_connected_at=_recently(),
        last_seen_at=_cold(),
        name="setup-gemini",
    )
    state = await resolve_play_setup_state(
        db_session, user, require=PlaySetupStage.NEEDS_MCP_CONNECTION
    )
    assert state.stage is PlaySetupStage.READY


@pytest.mark.asyncio
async def test_multi_agent_reports_nearest_gate_when_none_ready(
    db_session: AsyncSession,
) -> None:
    # Both unready: claude has NO_MCP_CONNECTION, hermes (non-MCP) is enabled but
    # cold → CONNECTED_NOT_LIVE. Most-ready (hermes) → NEEDS_LIVE under require=READY.
    user = await make_user(db_session, 7)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=None,
        name="cold-claude",
    )
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.HERMES,
        mcp_connected_at=None,
        last_seen_at=_cold(),
        name="cold-hermes",
    )
    state = await resolve_play_setup_state(
        db_session, user, require=PlaySetupStage.READY
    )
    assert state.stage is PlaySetupStage.NEEDS_LIVE


# ---------------------------------------------------------------------------
# Exclusions: bot / archived / provider IS NULL agents are ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_agent_is_excluded(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 8)
    await make_agent(db_session, user, name="house-bot", kind=AgentKind.BOT, connection=None)
    state = await resolve_play_setup_state(db_session, user)
    assert state.stage is PlaySetupStage.NEEDS_AGENT


@pytest.mark.asyncio
async def test_archived_agent_is_excluded(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 9)
    agent, _ = await _mcp_agent(
        db_session, user, provider=ConnectionProvider.CLAUDE, mcp_connected_at=_recently()
    )
    agent.archived_at = datetime.now(timezone.utc)
    await db_session.flush()
    state = await resolve_play_setup_state(db_session, user)
    assert state.stage is PlaySetupStage.NEEDS_AGENT


@pytest.mark.asyncio
async def test_provider_null_agent_is_now_seatable(db_session: AsyncSession) -> None:
    # Agents are decoupled from a provider — a provider-NULL AI agent is the new
    # normal and counts as a real agent. With no connection, the gate is
    # NEEDS_MCP_CONNECTION (the agent exists; the user just needs to connect an AI).
    user = await make_user(db_session, 10)
    agent, _ = await make_agent(db_session, user, name="no-provider", connection=None)
    agent.provider = None
    agent.kind = AgentKind.AI
    await db_session.flush()
    state = await resolve_play_setup_state(db_session, user)
    assert state.stage is PlaySetupStage.NEEDS_MCP_CONNECTION


# ---------------------------------------------------------------------------
# next_url correctness (incl. ?next= threading for a join target)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_url_threads_join_for_setup_gate(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 11)
    await _mcp_agent(db_session, user, provider=ConnectionProvider.CLAUDE, mcp_connected_at=None)
    match = await _make_registering_match(db_session, "m-join-1")
    state = await resolve_play_setup_state(
        db_session, user, target_match=match, require=PlaySetupStage.READY
    )
    assert state.stage is PlaySetupStage.NEEDS_MCP_CONNECTION
    # The connect page is generic now; the join next is threaded with a leading ?.
    assert state.next_url == (
        "/me/connections?next=/games/hoard-hurt-help/matches/m-join-1/join"
    )


@pytest.mark.asyncio
async def test_next_url_threads_join_for_needs_agent(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 12)
    match = await _make_registering_match(db_session, "m-join-2")
    state = await resolve_play_setup_state(db_session, user, target_match=match)
    assert state.stage is PlaySetupStage.NEEDS_AGENT
    # No query on the base, so the separator is ?.
    assert state.next_url == (
        "/me/agents/new?next=/games/hoard-hurt-help/matches/m-join-2/join"
    )


@pytest.mark.asyncio
async def test_next_url_ready_with_target_match_is_match_url(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 13)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_recently(),
        last_seen_at=_cold(),
    )
    match = await _make_registering_match(db_session, "m-ready-1")
    state = await resolve_play_setup_state(
        db_session, user, target_match=match, require=PlaySetupStage.NEEDS_MCP_CONNECTION
    )
    assert state.stage is PlaySetupStage.READY
    assert state.next_url == "/games/hoard-hurt-help/matches/m-ready-1"


# ---------------------------------------------------------------------------
# target_agent path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_target_agent_uses_overall_connection_readiness(
    db_session: AsyncSession,
) -> None:
    # A target agent no longer scopes to a provider — readiness comes from ANY of
    # the user's connections. A set-up-but-cold gemini connection makes the user
    # CONNECTED_NOT_LIVE, so under require=READY the gate is NEEDS_LIVE (not
    # NEEDS_MCP_CONNECTION) even though the target "claude" agent's own connection
    # is cold — because any connection can play any agent now.
    user = await make_user(db_session, 14)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.GEMINI,
        mcp_connected_at=_recently(),
        last_seen_at=_cold(),
        name="ready-gemini",
    )
    target, _ = await make_agent(db_session, user, name="plain-agent", connection=None)
    target.provider = None
    await db_session.flush()
    state = await resolve_play_setup_state(
        db_session, user, target_agent=target, require=PlaySetupStage.READY
    )
    assert state.stage is PlaySetupStage.NEEDS_LIVE


# ---------------------------------------------------------------------------
# Query bound (AD-4): single-provider READY user must not blow up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_provider_ready_query_bound(
    engine: AsyncEngine, db_session: AsyncSession
) -> None:
    """A single-provider ready user resolves with a small, bounded query count.

    Breakdown: 1 has-any-agent probe + 1 connection-providers query + the
    readiness cascade over the single provider (≤3 predicates). Crucially the
    count does NOT scale with the number of agents — the agent probe is a single
    LIMIT 1 and providers come from connections, not a per-agent loop. We assert
    <= 6 total queries, proving no per-agent blow-up.
    """
    user = await make_user(db_session, 15)
    # Two agents that SHARE one connection/provider — dedup must collapse them to
    # one distinct provider, so the readiness reduction runs at most once.
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = _recently()
    conn.last_seen_at = _cold()
    await db_session.flush()
    await make_agent(db_session, user, connection=conn, name="claude-a")
    await make_agent(db_session, user, connection=conn, name="claude-b")

    counter = {"n": 0}

    def _count(
        conn: SAConnection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext | None,
        executemany: bool,
    ) -> None:
        counter["n"] += 1

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        state = await resolve_play_setup_state(
            db_session, user, require=PlaySetupStage.NEEDS_MCP_CONNECTION
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert state.stage is PlaySetupStage.READY
    assert counter["n"] <= 6, f"expected <= 6 queries, got {counter['n']}"


# ---------------------------------------------------------------------------
# compute_nav_cta: the nav is dumb — any signed-in user gets "Play now" → lobby,
# regardless of MCP setup state. All gating moved to the join flow.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nav_cta_stale_mcp_is_still_play_now(db_session: AsyncSession) -> None:
    # 100-day-old mcp_connected_at is past the 90-day window (no current setup),
    # but the nav no longer reads setup state — it still shows "Play now".
    user = await make_user(db_session, 16)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_stale_100_days(),
        last_seen_at=_stale_100_days(),
    )
    cta = await compute_nav_cta(db_session, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"


@pytest.mark.asyncio
async def test_nav_cta_current_setup_is_play_now(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 17)
    await _mcp_agent(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_recently(),
        last_seen_at=_cold(),
    )
    cta = await compute_nav_cta(db_session, user)
    assert cta.label == "Play now"
    assert cta.href == "/games/hoard-hurt-help#lobby-upcoming"
