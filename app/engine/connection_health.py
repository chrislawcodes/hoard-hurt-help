"""Operational health for a Connection and the agents it powers."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionStatus
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission

_LIVE_WINDOW_SECONDS = 90
_HEARTBEAT_THROTTLE_SECONDS = 10


def _as_aware(dt: datetime) -> datetime:
    """SQLite may drop tzinfo on read; treat naive values as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _humanize_since(dt: datetime, now: datetime) -> str:
    """Return a small relative time string for the UI badge."""
    secs = int((now - _as_aware(dt)).total_seconds())
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
    """Resolve connection health from the connection and all of its agents."""
    now = now or datetime.now(timezone.utc)
    last_seen = connection.last_seen_at
    warm = (
        last_seen is not None
        and (now - _as_aware(last_seen)).total_seconds() <= _LIVE_WINDOW_SECONDS
    )
    last_connected = connection.last_seen_at or connection.first_connected_at
    never_connected = last_connected is None
    last_connected_at = (
        _as_aware(last_connected) if last_connected is not None else None
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

    agent_rows = (
        await db.execute(
            select(Agent.id)
            .where(
                Agent.connection_id == connection.id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
            )
        )
    ).all()
    active_agent_ids = [agent_id for (agent_id,) in agent_rows]
    if not active_agent_ids:
        if warm:
            return build(ConnectionHealth.READY)
        return build(ConnectionHealth.DISCONNECTED, needs_reconnect=True)

    player_rows = (
        (
            await db.execute(
                select(Match, Player)
                .join(Player, Player.match_id == Match.id)
                .join(Agent, Agent.id == Player.agent_id)
                .where(
                    Match.state == GameState.ACTIVE,
                    Player.left_at.is_(None),
                    Agent.connection_id == connection.id,
                    Agent.kind == AgentKind.AI,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.archived_at.is_(None),
                )
                .order_by(Match.id, Player.id)
            )
        )
        .all()
    )
    if not player_rows:
        if warm:
            return build(ConnectionHealth.READY, agent_count=len(active_agent_ids))
        return build(
            ConnectionHealth.DISCONNECTED,
            agent_count=len(active_agent_ids),
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
            agent_count=len(active_agent_ids),
            needs_reconnect=True,
        )

    live_match = next(iter(match_by_id.values()))
    return build(
        ConnectionHealth.LIVE,
        game=live_match,
        agent_count=len(active_agent_ids),
    )
