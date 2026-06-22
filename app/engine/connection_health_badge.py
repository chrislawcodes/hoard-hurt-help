"""Badge presentation and liveness for a Connection and the agents it powers.

This is the base layer of the connection-health surface: the operational-health
``ConnectionHealth`` badge state machine plus the small liveness primitives every
other layer reuses (``_within_window``, ``_connection_is_live``, the window
constants). It has no dependency on the provider-readiness or join-gate-capacity
layers — those build on top of it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionStatus
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission

LIVE_WINDOW_SECONDS = 90
_HEARTBEAT_THROTTLE_SECONDS = 10
# How recently the AI must have polled get_next_turn to count as "loop running".
# Generous: covers the ~25s long-poll hold PLUS an LLM's think-and-submit gap
# between polls, so a busy agent is never mistaken for a stopped one.
LOOP_RUNNING_WINDOW_SECONDS = 120


def _within_window(dt: datetime | None, now: datetime, window_seconds: int) -> bool:
    """True when *dt* is set and within *window_seconds* of *now*.

    The shared "warm / live" liveness check: a timestamp counts as fresh when it
    exists and is no older than the window. Both the connection-health and the
    bot-health state machines reduce their "is it alive right now?" question to
    this, differing only in which timestamp and which window they pass in
    (``last_seen_at`` + ``LIVE_WINDOW_SECONDS`` vs ``last_polled_at`` +
    ``LOOP_RUNNING_WINDOW_SECONDS``).
    """
    if dt is None:
        return False
    return (now - ensure_aware(dt)).total_seconds() <= window_seconds


def humanize_since(dt: datetime, now: datetime) -> str:
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


async def agent_is_defaulting(
    db: AsyncSession, agent_id: int, match_id: str, threshold: int
) -> bool:
    """True when this seat's last ``threshold`` submissions in the match all defaulted.

    Keyed on (agent_id, match_id), which uniquely identifies a seat, and ordered
    by (round, turn, id) descending so the window is selected deterministically.
    """
    flags = (
        (
            await db.execute(
                select(TurnSubmission.was_defaulted)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .join(Player, Player.id == TurnSubmission.player_id)
                .where(Player.agent_id == agent_id, Player.match_id == match_id)
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
    warm = _within_window(connection.last_seen_at, now, LIVE_WINDOW_SECONDS)
    last_connected = connection.last_seen_at or connection.first_connected_at
    never_connected = last_connected is None
    last_connected_at = (
        ensure_aware(last_connected) if last_connected is not None else None
    )
    last_connected_human = (
        None if last_connected is None else humanize_since(last_connected, now)
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

    # Agents this machine COVERS: all the user's active AI agents — any
    # connection can serve any agent now. Drives the badge's agent_count only.
    covered_count = (
        await db.execute(
            select(func.count())
            .select_from(Agent)
            .where(
                Agent.user_id == connection.user_id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
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
            if await agent_is_defaulting(db, player.agent_id, match_id, threshold):
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
