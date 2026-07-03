"""Tests for `provider_readiness` — the top-down readiness cascade (Slice 1).

Covers the four boundaries for an MCP provider (claude) and a non-MCP provider
(hermes), the PAUSED-only → CONNECTED_NOT_LIVE case, and the cascade-order stress
case (a non-MCP connection with a fresh last_polled_at but stale last_seen_at must
resolve LIVE, proving the cascade checks loop_running first). Also asserts that a
single `provider_readiness` call issues <= 3 queries (no hidden 7th predicate).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Connection as SAConnection
from sqlalchemy.engine.interfaces import ExecutionContext
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.engine.connection_health import ProviderReadiness, provider_readiness
from app.models import Base
from app.models.connection import ConnectionProvider, ConnectionStatus
from tests.factories import make_connection, make_user


def _recently() -> datetime:
    """A timestamp 20s ago — inside the live / loop-running windows."""
    return datetime.now(timezone.utc) - timedelta(seconds=20)


def _cold() -> datetime:
    """A timestamp 10 minutes ago — outside every window."""
    return datetime.now(timezone.utc) - timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Fixtures (local engine/session, matching test_coverage_health_and_join_gate.py)
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
# MCP provider (claude) — all four boundaries
# ---------------------------------------------------------------------------


async def test_mcp_live_when_recently_polled(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = _recently()
    conn.last_seen_at = _recently()
    conn.last_polled_at = _recently()
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.LIVE


async def test_mcp_seen_not_polling_when_seen_but_poll_stale(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 1)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = _recently()
    conn.last_seen_at = _recently()  # seen → live setup
    conn.last_polled_at = _cold()  # but no play loop running
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.SEEN_NOT_POLLING


async def test_mcp_connected_not_live_when_mcp_recent_but_cold(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 2)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = _recently()  # set up (recent MCP)
    conn.last_seen_at = _cold()  # not seen recently → not live
    conn.last_polled_at = None
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.CONNECTED_NOT_LIVE


async def test_mcp_no_connection_when_provider_absent(db_session: AsyncSession) -> None:
    """An MCP provider with no connection enabled at all has no current setup."""
    user = await make_user(db_session, 3)
    # A connection for a DIFFERENT provider must not satisfy claude.
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.HERMES)
    conn.last_seen_at = _recently()
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.NO_MCP_CONNECTION


async def test_mcp_no_connection_when_only_expired_mcp(db_session: AsyncSession) -> None:
    """An MCP-sign-in whose token aged out (no recent activity, no machine) is not
    set up: the OAuth-expiry semantics still hold for MCP-only users."""
    user = await make_user(db_session, 4)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    stale = datetime.now(timezone.utc) - timedelta(days=120)  # past the 90-day cutoff
    conn.mcp_connected_at = stale
    conn.first_connected_at = stale
    conn.last_seen_at = stale
    conn.last_polled_at = None
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.NO_MCP_CONNECTION


# ---------------------------------------------------------------------------
# MCP provider served by a MACHINE connection (the always-on connector /
# paste-in loop, mcp_connected_at IS NULL) — it must satisfy readiness too.
# ---------------------------------------------------------------------------


async def test_mcp_provider_live_via_machine_connection(
    db_session: AsyncSession,
) -> None:
    """A live, polling machine connection makes an MCP provider LIVE — so a held
    seat auto-confirms off the always-on connector with no MCP sign-in."""
    user = await make_user(db_session, 5)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = None  # machine connection, not an MCP sign-in
    conn.last_seen_at = _recently()
    conn.last_polled_at = _recently()  # the connector is looping
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.LIVE


async def test_mcp_provider_seen_not_polling_via_machine_connection(
    db_session: AsyncSession,
) -> None:
    """A machine connection seen recently but not polling → SEEN_NOT_POLLING."""
    user = await make_user(db_session, 6)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = None
    conn.last_seen_at = _recently()
    conn.last_polled_at = _cold()
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.SEEN_NOT_POLLING


async def test_mcp_provider_connected_not_live_via_cold_machine_connection(
    db_session: AsyncSession,
) -> None:
    """A machine connection set up but cold → CONNECTED_NOT_LIVE (set up, not live).
    No recency cutoff for machines: there's no OAuth token to expire."""
    user = await make_user(db_session, 7)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = None
    conn.last_seen_at = _cold()
    conn.last_polled_at = None
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.CONNECTED_NOT_LIVE


# ---------------------------------------------------------------------------
# Non-MCP provider (hermes) — all four boundaries
# ---------------------------------------------------------------------------


async def test_non_mcp_live_when_recently_polled(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 10)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.HERMES)
    conn.last_seen_at = _recently()
    conn.last_polled_at = _recently()
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.HERMES)
    assert result is ProviderReadiness.LIVE


async def test_non_mcp_live_when_polling_but_seen_stale_cascade_order(
    db_session: AsyncSession,
) -> None:
    """Cascade-order stress case: a non-MCP connection with a fresh last_polled_at
    but a STALE last_seen_at is genuinely LIVE. provider_has_live_current_setup
    (→ provider_is_covered, keyed on last_seen_at) is False here, so this only
    resolves LIVE because the cascade checks provider_loop_running FIRST.
    """
    user = await make_user(db_session, 11)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.HERMES)
    conn.last_seen_at = _cold()  # stale → "covered" would say NOT live
    conn.last_polled_at = _recently()  # but the play loop is running now
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.HERMES)
    assert result is ProviderReadiness.LIVE


async def test_non_mcp_seen_not_polling_when_seen_but_poll_stale(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 12)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.HERMES)
    conn.last_seen_at = _recently()  # covered → live setup
    conn.last_polled_at = _cold()  # but no play loop running
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.HERMES)
    assert result is ProviderReadiness.SEEN_NOT_POLLING


async def test_non_mcp_connected_not_live_when_enabled_but_cold(
    db_session: AsyncSession,
) -> None:
    """A non-MCP provider enabled on a stale connection has current setup
    (enabled-on-any, liveness-free) but is not live → CONNECTED_NOT_LIVE."""
    user = await make_user(db_session, 13)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.HERMES)
    conn.last_seen_at = _cold()  # not live
    conn.last_polled_at = None  # not looping
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.HERMES)
    assert result is ProviderReadiness.CONNECTED_NOT_LIVE


async def test_non_mcp_no_connection_when_provider_absent(
    db_session: AsyncSession,
) -> None:
    """User has no hermes connection at all → no current setup."""
    user = await make_user(db_session, 14)
    # A connection for a DIFFERENT provider must not satisfy hermes.
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.last_seen_at = _recently()
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.HERMES)
    assert result is ProviderReadiness.NO_MCP_CONNECTION


# ---------------------------------------------------------------------------
# PAUSED-only → CONNECTED_NOT_LIVE
# ---------------------------------------------------------------------------


async def test_paused_only_resolves_connected_not_live(
    db_session: AsyncSession,
) -> None:
    """A PAUSED connection with a recent mcp_connected_at: has current setup
    (which ignores PAUSED) but is neither live nor looping (both exclude PAUSED)
    → falls through to CONNECTED_NOT_LIVE, with no PAUSED special-case."""
    user = await make_user(db_session, 20)
    conn, _ = await make_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        status=ConnectionStatus.PAUSED,
    )
    conn.mcp_connected_at = _recently()
    conn.last_seen_at = _recently()
    conn.last_polled_at = _recently()
    await db_session.flush()

    result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert result is ProviderReadiness.CONNECTED_NOT_LIVE


# ---------------------------------------------------------------------------
# Query-count bound: one call must issue <= 3 queries (no hidden 7th predicate)
# ---------------------------------------------------------------------------


async def test_provider_readiness_issues_at_most_three_queries(
    engine: AsyncEngine, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, 30)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.mcp_connected_at = _recently()
    conn.last_seen_at = _recently()
    conn.last_polled_at = _cold()  # SEEN_NOT_POLLING → exercises 2 of 3 predicates
    await db_session.flush()

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

    from sqlalchemy import event

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        result = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert result is ProviderReadiness.SEEN_NOT_POLLING
    assert counter["n"] <= 3, f"expected <= 3 queries, got {counter['n']}"
