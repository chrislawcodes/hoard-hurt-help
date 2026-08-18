"""Badge presentation and liveness for a Connection and the agents it powers.

This is the base layer of the connection-health surface: the operational-health
``ConnectionHealth`` badge state machine plus the small liveness primitives every
other layer reuses (``within_window``, ``_connection_is_live``, the window
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
from app.engine.agent_playability import playable_agent_filter
from app.models.agent import Agent
from app.models.connection import Connection, ConnectionStatus
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission

LIVE_WINDOW_SECONDS = 90
_HEARTBEAT_THROTTLE_SECONDS = 10
# How recently the AI must have polled get_next_turn to count as "loop running".
# Must comfortably exceed the CONFIGURED long-poll hold — see
# `agent_long_poll_hold_seconds` in app/config.py, read into
# `agent_idle.LONG_POLL_HOLD_SECONDS` (40 by default, 90 in production) — plus
# an LLM's think-and-submit gap between polls, so a busy agent polling right at
# the hold's edge is never mistaken for a stopped one.
# tests/test_hold_measurement_settings.py pins that this window beats the
# setting's default. This used to hardcode "the ~25s long-poll hold" — exactly
# the kind of second, independently-drifting copy this fix removes (see
# mcp_server/mcp_tools.py for the same fix applied to the tool description).
LOOP_RUNNING_WINDOW_SECONDS = 120


def within_window(dt: datetime | None, now: datetime, window_seconds: int) -> bool:
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


# (label, badge_class, pulse, still_dot) per state. ``pulse`` animates the dot;
# ``still_dot`` shows a steady one. Both are decided here, in Python, because a
# template that re-derived "is this the steady-dot state?" from the state value
# is exactly how the badge drifted between pages.
_HEALTH_PRESENTATION: dict[ConnectionHealth, tuple[str, str, bool, bool]] = {
    ConnectionHealth.PAUSED: ("Paused", "badge-done", False, False),
    ConnectionHealth.STALLED: ("Stalled", "badge-alert", True, False),
    ConnectionHealth.LIVE: ("Live", "badge-ok", True, False),
    ConnectionHealth.READY: ("Ready", "badge-ok", False, True),
    ConnectionHealth.DISCONNECTED: ("Disconnected", "badge-alert", False, False),
}


@dataclass(frozen=True)
class ConnectionHealthStatus:
    """Resolved connection health plus the metadata rendered in the badge.

    ``pulse`` and ``still_dot`` are the badge's two dot modes, decided here so
    ``app/templates/fragments/_badge.html`` only ever renders fields.
    """

    state: ConnectionHealth
    label: str
    badge_class: str
    pulse: bool
    needs_reconnect: bool
    never_connected: bool
    last_connected_at: datetime | None
    last_connected_human: str | None
    still_dot: bool = False
    match_id: str | None = None
    game_name: str | None = None
    agent_count: int = 0


@dataclass(frozen=True)
class CalmConnectionStatus:
    """Low-alarm, type-aware status for the connections inventory list.

    The raw ``ConnectionHealth`` badge calls a normally-idle MCP client
    "Disconnected" in red. On the inventory list that misreads as a fault: an MCP
    client only stays live while its chat session is open, so idle is its normal
    resting state — not a problem to fix. This maps ``(state, is_mcp)`` to calm
    wording and reserves red for a genuine problem (a stalled connection that
    keeps missing turns).
    """

    label: str
    badge_class: str
    pulse: bool  # animate the dot (actively live)
    still_dot: bool  # show a steady dot (warm / ready)
    note: str | None  # calm one-line guidance for the row meta


def calm_connection_status(
    state: ConnectionHealth, *, is_mcp: bool, never_connected: bool = False
) -> CalmConnectionStatus:
    """Calm, type-aware presentation for one connection — the ONLY one.

    MCP (the AI chat app) rests as "Idle" — normal, grey, no nudge to fix. A
    machine helper rests as "Asleep" with a gentle restart nudge, because a
    background service meant to run 24/7 being off IS worth acting on. A
    connection that never finished connecting reads as a neutral "Not connected
    yet". Only a genuinely stalled connection shows red.

    This used to dress only the inventory *list*, while the connection detail
    page and its 15s badge poll rendered the raw ``_HEALTH_PRESENTATION`` words
    straight from ``compute_connection_health``. The same connection therefore
    read two ways depending on which page you were on — clearest in the
    never-connected case, where the list said a grey "Not connected yet" and the
    detail page an amber "Waiting to connect" (and then repeated the phrase in
    the meta line beside it), but present at every idle state too ("Idle" vs a
    red "Disconnected"). The detail page now maps through here as well, so a
    state can only look one way. The calm wording won because it is the
    deliberate, documented design: the raw badge's red for a normally-idle MCP
    client misreads as a fault, and red is reserved for a real problem.
    """
    if never_connected:
        return CalmConnectionStatus(
            "Not connected yet", "badge-done", False, False,
            "finish the steps above to connect",
        )
    if state is ConnectionHealth.STALLED:
        return CalmConnectionStatus(
            "Having trouble", "badge-alert", True, False,
            "kept missing turns — open your AI and check it's running",
        )
    if state is ConnectionHealth.PAUSED:
        return CalmConnectionStatus("Paused", "badge-done", False, False, None)
    if state is ConnectionHealth.LIVE:
        return CalmConnectionStatus(
            "Playing now" if is_mcp else "Running", "badge-ok", True, False, None
        )
    if state is ConnectionHealth.READY:
        return CalmConnectionStatus(
            "Ready" if is_mcp else "Running", "badge-ok", False, True, None
        )
    # DISCONNECTED — the common idle case, made calm and type-aware.
    if is_mcp:
        return CalmConnectionStatus(
            "Idle", "badge-done", False, False, "sign in to your AI to play"
        )
    return CalmConnectionStatus(
        "Asleep", "badge-soon", False, False, "restart the helper to play 24/7"
    )


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
    warm = within_window(connection.last_seen_at, now, LIVE_WINDOW_SECONDS)
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
        label, badge_class, pulse, still_dot = _HEALTH_PRESENTATION[state]
        return ConnectionHealthStatus(
            state=state,
            label=label,
            badge_class=badge_class,
            pulse=pulse,
            needs_reconnect=needs_reconnect,
            never_connected=never_connected,
            last_connected_at=last_connected_at,
            last_connected_human=last_connected_human,
            still_dot=still_dot,
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
                *playable_agent_filter(),
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
    return within_window(connection.last_seen_at, now, LIVE_WINDOW_SECONDS)
