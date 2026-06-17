"""Operational health for a Connection and the agents it powers."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission

LIVE_WINDOW_SECONDS = 90
_HEARTBEAT_THROTTLE_SECONDS = 10
# How recently the AI must have polled get_next_turn to count as "loop running".
# Generous: covers the ~25s long-poll hold PLUS an LLM's think-and-submit gap
# between polls, so a busy agent is never mistaken for a stopped one.
LOOP_RUNNING_WINDOW_SECONDS = 120
MCP_CONNECTION_VALID_DAYS = 90
MCP_CONNECTION_PROVIDERS = frozenset(
    {
        ConnectionProvider.CLAUDE,
        ConnectionProvider.OPENAI,
        ConnectionProvider.GEMINI,
    }
)


def _humanize_since(dt: datetime, now: datetime) -> str:
    """Return a small relative time string for the UI badge."""
    secs = int((now - ensure_aware(dt)).total_seconds())
    if secs < 10:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


class ConnectionHealth(str, enum.Enum):
    """Operational states shown on the connection badge."""

    PAUSED = "paused"
    STALLED = "stalled"
    LIVE = "live"
    READY = "ready"
    DISCONNECTED = "disconnected"


_HEALTH_PRESENTATION: dict[ConnectionHealth, tuple[str, str, bool]] = {
    ConnectionHealth.PAUSED: ("Paused", "badge-done", False),
    ConnectionHealth.STALLED: ("Stalled", "badge-alert", True),
    ConnectionHealth.LIVE: ("Live", "badge-ok", True),
    ConnectionHealth.READY: ("Ready", "badge-ok", False),
    ConnectionHealth.DISCONNECTED: ("Disconnected", "badge-alert", False),
}


@dataclass(frozen=True)
class ConnectionHealthStatus:
    """Resolved connection health plus the metadata rendered in the badge."""

    state: ConnectionHealth
    label: str
    badge_class: str
    pulse: bool
    needs_reconnect: bool
    never_connected: bool
    last_connected_at: datetime | None
    last_connected_human: str | None
    match_id: str | None = None
    game_name: str | None = None
    agent_count: int = 0


async def _is_defaulting(
    db: AsyncSession, player_id: int, match_id: str, threshold: int
) -> bool:
    """True when the player's last `threshold` submissions in this match defaulted."""
    flags = (
        (
            await db.execute(
                select(TurnSubmission.was_defaulted)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .where(
                    TurnSubmission.player_id == player_id,
                    Turn.match_id == match_id,
                )
                .order_by(Turn.round.desc(), Turn.turn.desc(), Turn.id.desc())
                .limit(threshold)
            )
        )
        .scalars()
        .all()
    )
    return len(flags) >= threshold and all(flags)


async def compute_connection_health(
    db: AsyncSession, connection: Connection, *, now: datetime | None = None
) -> ConnectionHealthStatus:
    """Resolve health from THIS connection's liveness and the matches pinned to it.

    Agents are no longer attached to a connection, so health keys off the
    connection's own liveness (``last_seen_at``) plus the matches it is currently
    serving via ``players.served_by_connection_id`` — not agent attachment. An
    idle-but-live machine (running, providers on, nothing pinned yet) is READY,
    which is correct: it can take work the moment a turn needs it. ``agent_count``
    reports how many of the user's active AI agents this machine *covers* (their
    provider is enabled here).
    """
    now = now or datetime.now(timezone.utc)
    last_seen = connection.last_seen_at
    warm = (
        last_seen is not None
        and (now - ensure_aware(last_seen)).total_seconds() <= LIVE_WINDOW_SECONDS
    )
    last_connected = connection.last_seen_at or connection.first_connected_at
    never_connected = last_connected is None
    last_connected_at = (
        ensure_aware(last_connected) if last_connected is not None else None
    )
    last_connected_human = (
        None if last_connected is None else _humanize_since(last_connected, now)
    )

    def build(
        state: ConnectionHealth,
        *,
        game: Match | None = None,
        agent_count: int = 0,
        needs_reconnect: bool = False,
    ) -> ConnectionHealthStatus:
        label, badge_class, pulse = _HEALTH_PRESENTATION[state]
        return ConnectionHealthStatus(
            state=state,
            label=label,
            badge_class=badge_class,
            pulse=pulse,
            needs_reconnect=needs_reconnect,
            never_connected=never_connected,
            last_connected_at=last_connected_at,
            last_connected_human=last_connected_human,
            match_id=game.id if game else None,
            game_name=game.name if game else None,
            agent_count=agent_count,
        )

    if connection.status == ConnectionStatus.PAUSED:
        return build(ConnectionHealth.PAUSED)

    # Agents this machine COVERS: the user's active AI agents whose provider is
    # enabled on this connection. Drives the badge's agent_count only.
    enabled_providers = (
        (
            await db.execute(
                select(ConnectionProviderRow.provider).where(
                    ConnectionProviderRow.connection_id == connection.id,
                    ConnectionProviderRow.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    covered_count = 0
    if enabled_providers:
        covered_count = (
            await db.execute(
                select(func.count())
                .select_from(Agent)
                .where(
                    Agent.user_id == connection.user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.archived_at.is_(None),
                    Agent.provider.in_(enabled_providers),
                )
            )
        ).scalar() or 0

    # Matches this connection is currently SERVING (the sticky pin).
    player_rows = (
        (
            await db.execute(
                select(Match, Player)
                .join(Player, Player.match_id == Match.id)
                .where(
                    Match.state == GameState.ACTIVE,
                    Player.left_at.is_(None),
                    Player.served_by_connection_id == connection.id,
                )
                .order_by(Match.id, Player.id)
            )
        )
        .all()
    )
    if not player_rows:
        # Live but idle (READY) or not seen recently (DISCONNECTED).
        if warm:
            return build(ConnectionHealth.READY, agent_count=covered_count)
        return build(
            ConnectionHealth.DISCONNECTED,
            agent_count=covered_count,
            needs_reconnect=True,
        )

    players_by_match: dict[str, list[Player]] = {}
    match_by_id: dict[str, Match] = {}
    for match, player in player_rows:
        match_by_id[match.id] = match
        players_by_match.setdefault(match.id, []).append(player)

    stalled_match: Match | None = None
    for match_id, players in players_by_match.items():
        if not warm:
            stalled_match = match_by_id[match_id]
            break
        threshold = max(1, connection.stall_threshold)
        for player in players:
            if await _is_defaulting(db, player.id, match_id, threshold):
                stalled_match = match_by_id[match_id]
                break
        if stalled_match is not None:
            break

    if stalled_match is not None:
        return build(
            ConnectionHealth.STALLED,
            game=stalled_match,
            agent_count=covered_count,
            needs_reconnect=True,
        )

    live_match = next(iter(match_by_id.values()))
    return build(
        ConnectionHealth.LIVE,
        game=live_match,
        agent_count=covered_count,
    )


# ---------------------------------------------------------------------------
# Coverage-aware helpers (provider-based, not attached-connection-based)
# ---------------------------------------------------------------------------


def _connection_is_live(connection: Connection, now: datetime) -> bool:
    """True when this connection counts as *live* for coverage purposes.

    A connection is live when:
    - not deleted (caller already filters deleted_at IS NULL)
    - status != PAUSED
    - last_seen_at is within LIVE_WINDOW_SECONDS of *now*
    """
    if connection.status == ConnectionStatus.PAUSED:
        return False
    last_seen = connection.last_seen_at
    if last_seen is None:
        return False
    aware = last_seen if last_seen.tzinfo is not None else last_seen.replace(tzinfo=timezone.utc)
    return (now - aware).total_seconds() <= LIVE_WINDOW_SECONDS


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
        await db.execute(
            select(Connection)
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
            select(Connection.id)
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
            .limit(1)
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
                select(Connection)
                .join(
                    ConnectionProviderRow,
                    ConnectionProviderRow.connection_id == Connection.id,
                )
                .where(
                    Connection.user_id == user_id,
                    Connection.deleted_at.is_(None),
                    Connection.mcp_connected_at.is_not(None),
                    ConnectionProviderRow.provider == provider,
                    ConnectionProviderRow.enabled.is_(True),
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
            select(Connection)
            .join(
                ConnectionProviderRow,
                ConnectionProviderRow.connection_id == Connection.id,
            )
            .where(
                Connection.user_id == user_id,
                Connection.deleted_at.is_(None),
                Connection.mcp_connected_at.is_not(None),
                ConnectionProviderRow.provider == provider,
                ConnectionProviderRow.enabled.is_(True),
            )
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
    query = (
        select(Connection.last_polled_at)
        .join(
            ConnectionProviderRow,
            ConnectionProviderRow.connection_id == Connection.id,
        )
        .where(
            Connection.user_id == user_id,
            Connection.deleted_at.is_(None),
            Connection.status != ConnectionStatus.PAUSED,
            ConnectionProviderRow.provider == provider,
            ConnectionProviderRow.enabled.is_(True),
        )
    )
    if provider_uses_mcp_connection(provider):
        query = query.where(Connection.mcp_connected_at.is_not(None))
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


async def enabled_provider_values_on_nonpaused_connections(
    db: AsyncSession, user_id: int
) -> set[str]:
    """Provider values enabled on any non-paused, non-deleted connection.

    This is the coverage signal for the agent list's ready-vs-needs-connecting
    state. A provider enabled only on a paused connection still needs
    reconnecting, so paused rows are excluded here on purpose.
    """
    rows = (
        (
            await db.execute(
                select(ConnectionProviderRow.provider)
                .join(Connection, Connection.id == ConnectionProviderRow.connection_id)
                .where(
                    Connection.user_id == user_id,
                    Connection.deleted_at.is_(None),
                    Connection.status != ConnectionStatus.PAUSED,
                    ConnectionProviderRow.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return {p.value for p in rows}


async def active_matches_for_provider(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> int:
    """Count active matches for AI agents of *user_id* whose provider is *provider*.

    Used by the SUM join-gate rule.
    """
    count = await db.scalar(
        select(func.count(func.distinct(Match.id)))
        .select_from(Agent)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .where(
            Agent.user_id == user_id,
            Agent.provider == provider,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state == GameState.ACTIVE,
        )
    )
    return int(count or 0)


async def live_provider_capacity(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> int:
    """Sum of max_concurrent_games over the user's live connections that have *provider* enabled.

    Returns 0 when no live connection covers the provider (join is always blocked).
    """
    now = datetime.now(timezone.utc)
    query = (
        select(Connection)
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
    if provider_uses_mcp_connection(provider):
        query = query.where(Connection.mcp_connected_at.is_not(None))
    rows = (await db.execute(query)).scalars().all()
    return sum(c.max_concurrent_games for c in rows if _connection_is_live(c, now))


def is_join_blocked(active_count: int, capacity_sum: int) -> bool:
    """Return True when the active count reaches or exceeds the combined capacity.

    DB-free helper — unit-testable without a session.
    capacity_sum == 0 means no live connection covers the provider → always blocked.
    """
    return active_count >= capacity_sum if capacity_sum > 0 else True
