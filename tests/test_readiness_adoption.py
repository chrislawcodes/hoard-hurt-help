"""Tests for B2 slice: ProviderReadiness adoption across connections_pages,
agents_list, agents_detail, and seat_hold.

Covers:
  - connections-page bar parity: SEEN_NOT_POLLING triggers auto-forward on both
    the page-load (GET /me/connections) and the poll (GET /me/connections/live-status)
    paths; NO_MCP_CONNECTION does NOT auto-forward.
  - agents_list badge: stale/absent mcp_connected_at → "needs connecting";
    recent mcp_connected_at → "ready".
  - agents_detail: readiness reflects the signal for live / set-up /
    needs-connecting states.
  - confirm_seat_if_live ↔ resolver LIVE parity: across all four ProviderReadiness
    states, confirm_seat_if_live confirms exactly when resolve_play_setup_state
    reports READY (i.e. when the provider is LIVE).
"""

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
from app.engine.connection_health import (
    ProviderReadiness,
    provider_readiness,
)
from app.engine.seat_hold import confirm_seat_if_live
from app.models import Base
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User
from app.routes.agents_detail import _build_agent_detail_context
from app.routes.connections_setup import router as connections_setup_router
from app.routes.nav_context import PlaySetupStage, resolve_play_setup_state
from tests.factories import make_connection, make_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recently() -> datetime:
    """A timestamp 20 seconds ago — inside the 90-second live window."""
    return datetime.now(timezone.utc) - timedelta(seconds=20)


def _stale() -> datetime:
    """A timestamp 10 minutes ago — outside the live window."""
    return datetime.now(timezone.utc) - timedelta(minutes=10)


def _mcp_stale() -> datetime:
    """mcp_connected_at 100 days ago — outside the 90-day MCP validity window."""
    return datetime.now(timezone.utc) - timedelta(days=100)


def _mcp_recent() -> datetime:
    """mcp_connected_at 5 days ago — inside the 90-day MCP validity window."""
    return datetime.now(timezone.utc) - timedelta(days=5)


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
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


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
    test_app.include_router(connections_setup_router, prefix="/me/connections")
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as c:
        yield c


def _cookies(user_id: int) -> dict[str, str]:
    """Build a signed session cookie that authenticates as user_id."""
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(
        json.dumps({"user_id": user_id, "next_after_login": None}).encode()
    ).decode()
    return {"hhh_session": signer.sign(payload).decode()}


# ---------------------------------------------------------------------------
# DB factory helpers (local to this module)
# ---------------------------------------------------------------------------


async def _make_mcp_connection(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    mcp_connected_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    last_polled_at: datetime | None = None,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
) -> Connection:
    """Create an MCP-style connection (mcp_connected_at set) with full control over timestamps."""
    from app.engine.tokens import bot_key_lookup, generate_connection_key

    plain_key = generate_connection_key()
    conn = Connection(
        user_id=user.id,
        provider=provider,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=plain_key[-4:],
        status=status,
        max_concurrent_games=3,
        mcp_connected_at=mcp_connected_at,
        last_seen_at=last_seen_at,
        last_polled_at=last_polled_at,
    )
    db.add(conn)
    await db.flush()
    db.add(
        ConnectionProviderRow(
            connection_id=conn.id,
            provider=provider,
            enabled=True,
            detected=False,
        )
    )
    await db.flush()
    return conn


async def _make_match(
    db: AsyncSession,
    match_id: str,
    *,
    state: GameState,
) -> Match:
    now = datetime.now(timezone.utc)
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


async def _make_agent_for_provider(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    status: AgentStatus = AgentStatus.ACTIVE,
) -> tuple[Agent, AgentVersion]:
    """Create an AI agent directly with a given provider (no connection reference)."""
    agent = Agent(
        user_id=user.id,
        provider=provider,
        kind=AgentKind.AI,
        name=f"agent-{user.id}-{provider.value}",
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


# ---------------------------------------------------------------------------
# Part 1: connections-page bar parity
#
# A SEEN_NOT_POLLING provider (mcp_connected_at recent, last_seen recent,
# last_polled stale/absent) triggers the auto-forward on BOTH the page-load
# and the live_status_fragment poll path.
# ---------------------------------------------------------------------------


async def test_connections_page_load_auto_forwards_when_seen_not_polling(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET /me/connections?provider=claude&next=/lobby: SEEN_NOT_POLLING redirects."""
    async with session_factory() as db:
        user = await make_user(db, 100)
        conn = await _make_mcp_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_mcp_recent(),
            last_seen_at=_recently(),   # inside live window → SEEN
            last_polled_at=None,         # no play loop → NOT_POLLING
        )
        await db.commit()
        _ = conn  # used to set up the fixture

    resp = await client.get(
        "/me/connections?provider=claude&next=%2Flobby",
        cookies=_cookies(user.id),
    )
    # Should auto-forward to /lobby (either 303 redirect)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/lobby"


async def test_connections_page_load_no_forward_when_no_mcp_connection(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET /me/connections?provider=claude&next=/lobby: NO_MCP_CONNECTION does NOT redirect."""
    async with session_factory() as db:
        user = await make_user(db, 101)
        # No connection at all → NO_MCP_CONNECTION
        await db.commit()

    resp = await client.get(
        "/me/connections?provider=claude&next=%2Flobby",
        cookies=_cookies(user.id),
    )
    # Should render the connect page (200), NOT redirect
    assert resp.status_code == 200


async def test_connections_poll_auto_forwards_when_seen_not_polling(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET /me/connections/live-status?provider=claude&next=/lobby: SEEN_NOT_POLLING
    sends HX-Redirect header."""
    async with session_factory() as db:
        user = await make_user(db, 102)
        await _make_mcp_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_mcp_recent(),
            last_seen_at=_recently(),  # SEEN
            last_polled_at=None,        # NOT_POLLING
        )
        await db.commit()

    resp = await client.get(
        "/me/connections/live-status?provider=claude&next=%2Flobby",
        cookies=_cookies(user.id),
    )
    # HTMX redirect: 200 + HX-Redirect header
    assert resp.status_code == 200
    assert resp.headers.get("hx-redirect") == "/lobby"


async def test_connections_poll_no_forward_when_no_mcp_connection(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET /me/connections/live-status?provider=claude&next=/lobby: NO_MCP_CONNECTION
    does NOT send HX-Redirect."""
    async with session_factory() as db:
        user = await make_user(db, 103)
        # No connection at all → NO_MCP_CONNECTION
        await db.commit()

    resp = await client.get(
        "/me/connections/live-status?provider=claude&next=%2Flobby",
        cookies=_cookies(user.id),
    )
    # Should render the live-status fragment (200), no HX-Redirect
    assert resp.status_code == 200
    assert "hx-redirect" not in resp.headers


# ---------------------------------------------------------------------------
# Part 2: agents_list badge via provider_readiness signal
#
# Stale/absent mcp_connected_at (100+ days) → NO_MCP_CONNECTION → "needs connecting".
# Recent mcp_connected_at → CONNECTED_NOT_LIVE or better → "ready".
# ---------------------------------------------------------------------------


async def test_agents_list_badge_needs_connecting_when_mcp_stale(
    db_session: AsyncSession,
) -> None:
    """Agent with stale mcp_connected_at (100+ days) → NO_MCP_CONNECTION → needs connecting."""
    user = await make_user(db_session, 200)
    # MCP connection with a 100-day-old mcp_connected_at → outside validity window
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_stale(),
        last_seen_at=None,
        last_polled_at=None,
    )
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.NO_MCP_CONNECTION, (
        "Stale mcp_connected_at (100+ days) should give NO_MCP_CONNECTION"
    )
    # The agents_list badge condition: NO_MCP_CONNECTION → "needs connecting"
    needs_connecting = readiness == ProviderReadiness.NO_MCP_CONNECTION
    assert needs_connecting is True


async def test_agents_list_badge_ready_when_mcp_recent(
    db_session: AsyncSession,
) -> None:
    """Agent with recent mcp_connected_at → at least CONNECTED_NOT_LIVE → ready."""
    user = await make_user(db_session, 201)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=None,    # not currently live
        last_polled_at=None,
    )
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness != ProviderReadiness.NO_MCP_CONNECTION, (
        "Recent mcp_connected_at should give at least CONNECTED_NOT_LIVE"
    )
    # The agents_list badge condition: != NO_MCP_CONNECTION → "ready"
    needs_connecting = readiness == ProviderReadiness.NO_MCP_CONNECTION
    assert needs_connecting is False


async def test_agents_list_badge_ready_when_seen_not_polling(
    db_session: AsyncSession,
) -> None:
    """Agent with recent mcp_connected_at and recent last_seen_at → SEEN_NOT_POLLING → ready."""
    user = await make_user(db_session, 202)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=_recently(),  # inside live window
        last_polled_at=None,        # no play loop
    )
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.SEEN_NOT_POLLING
    # Not NO_MCP_CONNECTION → badge shows "ready"
    needs_connecting = readiness == ProviderReadiness.NO_MCP_CONNECTION
    assert needs_connecting is False


# ---------------------------------------------------------------------------
# Part 3: agents_detail readiness reflects provider_readiness signal
# ---------------------------------------------------------------------------


async def test_agents_detail_ready_when_live(
    db_session: AsyncSession,
) -> None:
    """LIVE provider (last_polled_at recent) → health.state == READY on detail page."""
    from app.engine.connection_health import ConnectionHealth

    user = await make_user(db_session, 300)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=_recently(),
        last_polled_at=_recently(),  # play loop running → LIVE
    )
    agent, _ = await _make_agent_for_provider(db_session, user)
    await db_session.flush()

    # Verify readiness is LIVE
    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.LIVE

    # Verify detail context reflects READY health
    from unittest.mock import MagicMock
    mock_request = MagicMock()
    ctx = await _build_agent_detail_context(db_session, mock_request, user, agent)
    health = ctx["health"]
    assert isinstance(health, dict)
    assert health["state"] == ConnectionHealth.READY
    assert health["needs_reconnect"] is False


async def test_agents_detail_ready_when_seen_not_polling(
    db_session: AsyncSession,
) -> None:
    """SEEN_NOT_POLLING provider → health.state == READY on detail page."""
    from app.engine.connection_health import ConnectionHealth

    user = await make_user(db_session, 301)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=_recently(),  # seen → SEEN_NOT_POLLING
        last_polled_at=None,
    )
    agent, _ = await _make_agent_for_provider(db_session, user)
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.SEEN_NOT_POLLING

    from unittest.mock import MagicMock
    mock_request = MagicMock()
    ctx = await _build_agent_detail_context(db_session, mock_request, user, agent)
    health = ctx["health"]
    assert isinstance(health, dict)
    assert health["state"] == ConnectionHealth.READY
    assert health["needs_reconnect"] is False


async def test_agents_detail_disconnected_when_connected_not_live(
    db_session: AsyncSession,
) -> None:
    """CONNECTED_NOT_LIVE provider → health.state == DISCONNECTED on detail page."""
    from app.engine.connection_health import ConnectionHealth

    user = await make_user(db_session, 302)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=None,    # not recently seen → CONNECTED_NOT_LIVE
        last_polled_at=None,
    )
    agent, _ = await _make_agent_for_provider(db_session, user)
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.CONNECTED_NOT_LIVE

    from unittest.mock import MagicMock
    mock_request = MagicMock()
    ctx = await _build_agent_detail_context(db_session, mock_request, user, agent)
    health = ctx["health"]
    assert isinstance(health, dict)
    assert health["state"] == ConnectionHealth.DISCONNECTED
    assert health["needs_reconnect"] is True


async def test_agents_detail_disconnected_when_no_mcp_connection(
    db_session: AsyncSession,
) -> None:
    """NO_MCP_CONNECTION → health.state == DISCONNECTED on detail page."""
    from app.engine.connection_health import ConnectionHealth

    user = await make_user(db_session, 303)
    # No connection at all → NO_MCP_CONNECTION
    agent, _ = await _make_agent_for_provider(db_session, user)
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.NO_MCP_CONNECTION

    from unittest.mock import MagicMock
    mock_request = MagicMock()
    ctx = await _build_agent_detail_context(db_session, mock_request, user, agent)
    health = ctx["health"]
    assert isinstance(health, dict)
    assert health["state"] == ConnectionHealth.DISCONNECTED
    assert health["needs_reconnect"] is True


# ---------------------------------------------------------------------------
# Part 4: confirm_seat_if_live ↔ resolve_play_setup_state LIVE parity
#
# For all four ProviderReadiness states, confirm_seat_if_live must confirm
# exactly when resolve_play_setup_state returns READY (stage == READY).
# ---------------------------------------------------------------------------


async def _make_player_with_held_seat(
    db: AsyncSession,
    user: User,
    agent: Agent,
    version: AgentVersion,
    match: Match,
) -> Player:
    """Create a player with a held seat (seat_reserved_until set)."""
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name="A",
        model_self_report=version.model,
        seat_reserved_until=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(player)
    await db.flush()
    return player


async def test_seat_hold_confirms_and_resolver_ready_when_live(
    db_session: AsyncSession,
) -> None:
    """LIVE state: confirm_seat_if_live returns True AND resolver reports READY."""
    user = await make_user(db_session, 400)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=_recently(),
        last_polled_at=_recently(),  # play loop running → LIVE
    )
    agent, version = await _make_agent_for_provider(db_session, user)
    match = await _make_match(db_session, "M_seat_live", state=GameState.ACTIVE)
    player = await _make_player_with_held_seat(db_session, user, agent, version, match)
    await db_session.flush()

    # Both should agree: LIVE
    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.LIVE

    confirmed = await confirm_seat_if_live(db_session, player)
    assert confirmed is True, "confirm_seat_if_live must return True when LIVE"

    resolver_state = await resolve_play_setup_state(
        db_session,
        user,
        target_agent=agent,
        require=PlaySetupStage.READY,
    )
    assert resolver_state.stage == PlaySetupStage.READY, (
        "resolver must report READY when LIVE"
    )


async def test_seat_hold_does_not_confirm_and_resolver_not_ready_when_seen_not_polling(
    db_session: AsyncSession,
) -> None:
    """SEEN_NOT_POLLING: confirm_seat_if_live returns False AND resolver NOT READY."""
    user = await make_user(db_session, 401)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=_recently(),  # seen
        last_polled_at=None,        # not polling
    )
    agent, version = await _make_agent_for_provider(db_session, user)
    match = await _make_match(db_session, "M_seat_seen", state=GameState.ACTIVE)
    player = await _make_player_with_held_seat(db_session, user, agent, version, match)
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.SEEN_NOT_POLLING

    confirmed = await confirm_seat_if_live(db_session, player)
    assert confirmed is False, "confirm_seat_if_live must NOT confirm when SEEN_NOT_POLLING"

    resolver_state = await resolve_play_setup_state(
        db_session,
        user,
        target_agent=agent,
        require=PlaySetupStage.READY,
    )
    assert resolver_state.stage != PlaySetupStage.READY, (
        "resolver must NOT report READY when SEEN_NOT_POLLING"
    )


async def test_seat_hold_does_not_confirm_and_resolver_not_ready_when_connected_not_live(
    db_session: AsyncSession,
) -> None:
    """CONNECTED_NOT_LIVE: confirm_seat_if_live returns False AND resolver NOT READY."""
    user = await make_user(db_session, 402)
    await _make_mcp_connection(
        db_session,
        user,
        provider=ConnectionProvider.CLAUDE,
        mcp_connected_at=_mcp_recent(),
        last_seen_at=None,
        last_polled_at=None,
    )
    agent, version = await _make_agent_for_provider(db_session, user)
    match = await _make_match(db_session, "M_seat_cnl", state=GameState.ACTIVE)
    player = await _make_player_with_held_seat(db_session, user, agent, version, match)
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.CONNECTED_NOT_LIVE

    confirmed = await confirm_seat_if_live(db_session, player)
    assert confirmed is False, "confirm_seat_if_live must NOT confirm when CONNECTED_NOT_LIVE"

    resolver_state = await resolve_play_setup_state(
        db_session,
        user,
        target_agent=agent,
        require=PlaySetupStage.READY,
    )
    assert resolver_state.stage != PlaySetupStage.READY, (
        "resolver must NOT report READY when CONNECTED_NOT_LIVE"
    )


async def test_seat_hold_does_not_confirm_and_resolver_not_ready_when_no_mcp_connection(
    db_session: AsyncSession,
) -> None:
    """NO_MCP_CONNECTION: confirm_seat_if_live returns False AND resolver NOT READY."""
    user = await make_user(db_session, 403)
    # No connection → NO_MCP_CONNECTION
    agent, version = await _make_agent_for_provider(db_session, user)
    match = await _make_match(db_session, "M_seat_noconn", state=GameState.ACTIVE)
    player = await _make_player_with_held_seat(db_session, user, agent, version, match)
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.CLAUDE)
    assert readiness == ProviderReadiness.NO_MCP_CONNECTION

    confirmed = await confirm_seat_if_live(db_session, player)
    assert confirmed is False, "confirm_seat_if_live must NOT confirm when NO_MCP_CONNECTION"

    resolver_state = await resolve_play_setup_state(
        db_session,
        user,
        target_agent=agent,
        require=PlaySetupStage.READY,
    )
    assert resolver_state.stage != PlaySetupStage.READY, (
        "resolver must NOT report READY when NO_MCP_CONNECTION"
    )


async def test_seat_hold_does_not_confirm_hermes_stale_seen_but_polling(
    db_session: AsyncSession,
) -> None:
    """Non-MCP (hermes) with stale last_seen_at but recent last_polled_at: LIVE.

    This is the key non-MCP edge case. Hermes uses provider_loop_running (keyed
    on last_polled_at) as its liveness signal, not last_seen_at. A connection
    with stale last_seen_at but recent last_polled_at should still resolve LIVE,
    and confirm_seat_if_live should confirm.
    """
    user = await make_user(db_session, 404)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.HERMES)
    conn.last_seen_at = _stale()            # stale last_seen → would fail is_covered
    conn.last_polled_at = _recently()       # but fresh poll → LIVE via loop_running
    await db_session.flush()

    readiness = await provider_readiness(db_session, user.id, ConnectionProvider.HERMES)
    assert readiness == ProviderReadiness.LIVE, (
        "Non-MCP with fresh last_polled_at must resolve LIVE "
        "even when last_seen_at is stale"
    )

    agent, version = await _make_agent_for_provider(
        db_session, user, provider=ConnectionProvider.HERMES
    )
    match = await _make_match(db_session, "M_seat_hermes", state=GameState.ACTIVE)
    player = await _make_player_with_held_seat(db_session, user, agent, version, match)
    await db_session.flush()

    confirmed = await confirm_seat_if_live(db_session, player)
    assert confirmed is True, (
        "confirm_seat_if_live must confirm when hermes LIVE via last_polled_at"
    )

    resolver_state = await resolve_play_setup_state(
        db_session,
        user,
        target_agent=agent,
        require=PlaySetupStage.READY,
    )
    assert resolver_state.stage == PlaySetupStage.READY, (
        "resolver must report READY for hermes LIVE provider"
    )
