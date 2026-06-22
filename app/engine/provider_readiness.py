"""Provider-based coverage and play-readiness for a user's connections.

Builds on ``connection_health_badge`` (liveness primitives and the window
constants). Answers "is this provider set up / live / actually looping?" via a
shared connections query, and resolves the ``ProviderReadiness`` ladder. The
join-gate-capacity layer builds on top of this.
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.aware_datetime import ensure_aware
from app.engine.connection_health_badge import (
    LOOP_RUNNING_WINDOW_SECONDS,
    _connection_is_live,
)
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow

MCP_CONNECTION_VALID_DAYS = 90
MCP_CONNECTION_PROVIDERS = frozenset(
    {
        ConnectionProvider.CLAUDE,
        ConnectionProvider.OPENAI,
        ConnectionProvider.GEMINI,
    }
)


def _provider_connections_query(
    user_id: int,
    provider: ConnectionProvider,
    *columns: Any,
    require_mcp: bool = False,
    require_mcp_when_provider_uses: bool = False,
    exclude_paused: bool = False,
) -> Select[Any]:
    """Build the shared "connections that have *provider* enabled" query.

    Every coverage predicate reduces over the same join + WHERE: a user's
    non-deleted connections joined to ``connection_providers`` rows for *provider*
    that are enabled. ``*columns`` are the SELECT targets (the whole ``Connection``
    entity, ``Connection.id``, ``Connection.last_polled_at`` — whatever the caller
    needs). The flags add the small per-predicate variations verbatim:

    - ``require_mcp`` — always require ``mcp_connected_at IS NOT NULL`` (the
      MCP-only predicates).
    - ``require_mcp_when_provider_uses`` — require ``mcp_connected_at IS NOT NULL``
      only when ``provider_uses_mcp_connection(provider)`` (the gate clause that
      appeared verbatim across several predicates).
    - ``exclude_paused`` — also require ``status != PAUSED`` at the DB level.
    """
    query: Select[Any] = (
        select(*columns)
        .join(
            ConnectionProviderRow,
            ConnectionProviderRow.connection_id == Connection.id,
        )
        .where(
            Connection.user_id == user_id,
            Connection.deleted_at.is_(None),
            ConnectionProviderRow.provider == provider,
            ConnectionProviderRow.enabled.is_(True),
        )
    )
    if exclude_paused:
        query = query.where(Connection.status != ConnectionStatus.PAUSED)
    if require_mcp or (require_mcp_when_provider_uses and provider_uses_mcp_connection(provider)):
        query = query.where(Connection.mcp_connected_at.is_not(None))
    return query


async def provider_is_covered(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> bool:
    """True when the user has at least one *live* connection with *provider* enabled.

    A live connection satisfies all three conditions:
    - deleted_at IS NULL
    - status != PAUSED
    - last_seen_at within LIVE_WINDOW_SECONDS of now
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(_provider_connections_query(user_id, provider, Connection))
    ).scalars().all()
    return any(_connection_is_live(c, now) for c in rows)


async def provider_enabled_on_any_connection(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> bool:
    """True when *provider* is enabled on at least one of the user's connections.

    Liveness is NOT required — this answers "could this agent ever be served from
    a machine I've set up?", which is the gate for whether a seatable agent
    exists. A connection that is merely stale (not seen recently) still counts;
    bringing it back online is the *next* step. Deleted connections are excluded.
    """
    row = (
        await db.execute(
            _provider_connections_query(user_id, provider, Connection.id).limit(1)
        )
    ).first()
    return row is not None


def provider_uses_mcp_connection(provider: ConnectionProvider) -> bool:
    """True when this provider's user-facing setup path is OAuth MCP."""
    return provider in MCP_CONNECTION_PROVIDERS


async def provider_has_recent_mcp_connection(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> bool:
    """True when *provider* has an MCP connection used in the last 90 days.

    For Claude, OpenAI, and Gemini, "set up" means the user has connected that
    provider's MCP client recently enough that the Google OAuth token should
    still be valid. A machine/header-style connection does not satisfy this
    check.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=MCP_CONNECTION_VALID_DAYS)
    rows = (
        (
            await db.execute(
                _provider_connections_query(
                    user_id, provider, Connection, require_mcp=True
                )
            )
        )
        .scalars()
        .all()
    )
    for connection in rows:
        seen_values = [
            dt
            for dt in (
                connection.last_seen_at,
                connection.first_connected_at,
                connection.mcp_connected_at,
            )
            if dt is not None
        ]
        if seen_values and max(ensure_aware(dt) for dt in seen_values) >= cutoff:
            return True
    return False


async def provider_has_current_setup(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> bool:
    """True when the provider has the setup path we currently support.

    Claude/OpenAI/Gemini are now MCP-first, so they require a recent MCP
    connection. Hermes/OpenClaw still use the older connection signal until their
    MCP setup path is handled separately.
    """
    if provider_uses_mcp_connection(provider):
        return await provider_has_recent_mcp_connection(db, user_id, provider)
    return await provider_enabled_on_any_connection(db, user_id, provider)


async def provider_has_live_current_setup(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> bool:
    """True when the provider's current setup path is connected right now."""
    if not provider_uses_mcp_connection(provider):
        return await provider_is_covered(db, user_id, provider)
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            _provider_connections_query(user_id, provider, Connection, require_mcp=True)
        )
    ).scalars().all()
    return any(_connection_is_live(connection, now) for connection in rows)


async def provider_loop_running(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> bool:
    """True when an AI is actually *running the play loop* for *provider*.

    Keys off ``last_polled_at`` (only ``get_next_turn`` bumps it) on a non-paused,
    non-deleted connection — so this answers "is an agent playing right now",
    unlike ``provider_is_covered`` which keys off ``last_seen_at`` and so treats a
    one-off sign-in handshake as "live". This is the gate for confirming a seat: a
    seat only auto-confirms when an AI is genuinely looping; otherwise it's held
    while the user starts their AI.
    """
    now = datetime.now(timezone.utc)
    query = _provider_connections_query(
        user_id,
        provider,
        Connection.last_polled_at,
        exclude_paused=True,
        require_mcp_when_provider_uses=True,
    )
    polled = (
        (
            await db.execute(query)
        )
        .scalars()
        .all()
    )
    for last_polled in polled:
        if last_polled is None:
            continue
        aware = (
            last_polled
            if last_polled.tzinfo is not None
            else last_polled.replace(tzinfo=timezone.utc)
        )
        if (now - aware).total_seconds() <= LOOP_RUNNING_WINDOW_SECONDS:
            return True
    return False


class ProviderReadiness(str, enum.Enum):
    """How ready a provider is to actually play, as a single ladder rung.

    Ordered worst→best in intent: ``NO_MCP_CONNECTION`` < ``CONNECTED_NOT_LIVE``
    < ``SEEN_NOT_POLLING`` < ``LIVE``. ``provider_readiness`` resolves the rung.
    """

    NO_MCP_CONNECTION = "no_mcp_connection"
    CONNECTED_NOT_LIVE = "connected_not_live"
    SEEN_NOT_POLLING = "seen_not_polling"
    LIVE = "live"


async def provider_readiness(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> ProviderReadiness:
    """Resolve a single readiness rung for *provider* as a top-down cascade.

    First match wins, evaluating highest readiness first:

    - ``provider_loop_running`` → ``LIVE`` (an AI is polling get_next_turn now)
    - ``provider_has_live_current_setup`` → ``SEEN_NOT_POLLING`` (connected and
      seen recently, but no play loop running)
    - ``provider_has_current_setup`` → ``CONNECTED_NOT_LIVE`` (set up, but not
      seen live right now)
    - otherwise → ``NO_MCP_CONNECTION`` (no usable setup at all)

    The cascade order is load-bearing for **non-MCP providers** (hermes/openclaw),
    whose predicates fall back to liveness-free / ``last_seen_at``-based checks. A
    non-MCP connection with a fresh ``last_polled_at`` but a stale ``last_seen_at``
    is genuinely ``LIVE`` even though ``provider_has_live_current_setup`` (→
    ``provider_is_covered``, which keys on ``last_seen_at``) is False. Checking
    ``provider_loop_running`` first makes that case resolve correctly. Evaluating
    the predicates in any other order could let a lower rung win over ``LIVE``.

    A PAUSED-only connection naturally lands in ``CONNECTED_NOT_LIVE``: there is no
    PAUSED special-case here. ``provider_has_current_setup`` ignores PAUSED while
    ``provider_has_live_current_setup`` and ``provider_loop_running`` exclude it,
    so the cascade falls through to the third rung on its own.

    Adds no new SQL — this is a thin cascade over the three existing predicates.
    """
    if await provider_loop_running(db, user_id, provider):
        return ProviderReadiness.LIVE
    if await provider_has_live_current_setup(db, user_id, provider):
        return ProviderReadiness.SEEN_NOT_POLLING
    if await provider_has_current_setup(db, user_id, provider):
        return ProviderReadiness.CONNECTED_NOT_LIVE
    return ProviderReadiness.NO_MCP_CONNECTION


async def enabled_provider_values(db: AsyncSession, user_id: int) -> set[str]:
    """Provider values enabled on at least one of the user's live-or-not
    connections — the providers an agent can be created for.

    Shared by the create-agent flow (which providers to offer) and the join hub
    (whether to send a setup-less user to connect a client first). Liveness is
    not required: a stale-but-set-up connection still counts. Deleted connections
    are excluded.
    """
    rows = (
        (
            await db.execute(
                select(ConnectionProviderRow.provider)
                .join(Connection, Connection.id == ConnectionProviderRow.connection_id)
                .where(
                    Connection.user_id == user_id,
                    Connection.deleted_at.is_(None),
                    ConnectionProviderRow.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return {p.value for p in rows}


async def user_play_readiness(db: AsyncSession, user_id: int) -> ProviderReadiness:
    """Best play-readiness across every provider the user has connected.

    The user is as ready as their most-ready connection. ``NO_MCP_CONNECTION``
    when nothing is set up.
    """
    rank = {
        ProviderReadiness.NO_MCP_CONNECTION: 0,
        ProviderReadiness.CONNECTED_NOT_LIVE: 1,
        ProviderReadiness.SEEN_NOT_POLLING: 2,
        ProviderReadiness.LIVE: 3,
    }
    best = ProviderReadiness.NO_MCP_CONNECTION
    for value in await enabled_provider_values(db, user_id):
        readiness = await provider_readiness(db, user_id, ConnectionProvider(value))
        if rank[readiness] > rank[best]:
            best = readiness
        if best == ProviderReadiness.LIVE:
            break
    return best
