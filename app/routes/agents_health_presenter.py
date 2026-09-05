"""Shared presentation helpers for the agent list and detail pages.

Holds the small value objects the agent templates read, the per-agent match
count query both pages need, the readiness-to-badge mapping both pages render,
and the readiness check that decides whether an agent can accept a new match
invitation. Kept separate so the list, create, and detail route modules can
share it without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.deps import DbSession
from app.engine.connection_health_badge import ConnectionHealth, ConnectionHealthStatus
from app.engine.provider_readiness import PlayVerdict, ProviderReadiness, play_verdict
from app.models.agent import Agent, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.match import GameState
from app.models.player import Player
from app.read_models.version_stats import VersionMatchLink, VersionStats


def readiness_health_status(
    readiness: ProviderReadiness, agent_status: AgentStatus
) -> ConnectionHealthStatus:
    """Map a user's play-readiness rung plus the agent's own status to the one
    health badge both the agent list and detail pages show.

    This is the single mapping both pages call. They used to hand-build it
    separately and had drifted: the list page badged every rung above
    ``NO_MCP_CONNECTION`` "Ready", so a ``CONNECTED_NOT_LIVE`` agent read as a
    green "Ready" on ``/me/agents`` but a red "No live connection" on its own
    detail page — the list telling the user their agent would play when nothing
    could pick up its turn.

    The ready / not-ready call is not made here: it is
    ``play_verdict(readiness)``, the one verdict the join picker
    (``app/routes/web_join.py``) reads too, so the same provider can no longer be
    "Ready" on ``/me/agents`` and ranked below ready on the join screen. Only
    ``PlayVerdict.READY`` (i.e. ``ProviderReadiness.LIVE``) is green.

    **``SEEN_NOT_POLLING`` is not "Ready" any more.** It means the client has
    been *seen* — any authenticated call, a bare sign-in handshake included —
    but has not polled for a turn. A turn is only ever handed out inside a poll,
    so nothing picks one up at this rung. Every gate that decides whether play
    can happen already agrees: a seat joined at this rung is *held*, not
    confirmed, and is deleted when the hold expires
    (``app/engine/seat_hold.py``); the seat-connect screen keeps it on the
    "start your AI" wait page; the nav resolver maps it to ``NEEDS_LIVE``. See
    ``play_verdict`` for the full derivation.

    The badge words, per rung:

    - A ``PAUSED`` agent shows "Paused" regardless of readiness, so the detail
      page's dedicated paused card keeps owning that state.
    - ``NO_MCP_CONNECTION`` and ``CONNECTED_NOT_LIVE`` read "No live connection"
      in red — unchanged from PR #644.
    - ``SEEN_NOT_POLLING`` reads "Not playing yet" in amber. It gets its own
      words rather than joining the red pair, because at this rung the client
      genuinely *is* connected — the connect page says so and moves the user on
      — so "No live connection" would be a fresh lie in the other direction.
      Amber is the repo's existing "needs a nudge, not broken" colour.
    - ``LIVE`` reads "Ready" in green with a steady dot: an AI is looping, so a
      turn gets picked up now.
    """
    if agent_status == AgentStatus.PAUSED:
        return ConnectionHealthStatus(
            state=ConnectionHealth.PAUSED,
            label="Paused",
            badge_class="badge-done",
            pulse=False,
            needs_reconnect=False,
            never_connected=False,
            last_connected_at=None,
            last_connected_human=None,
            still_dot=False,
        )
    if play_verdict(readiness) is PlayVerdict.READY:
        return ConnectionHealthStatus(
            state=ConnectionHealth.READY,
            label="Ready",
            badge_class="badge-ok",
            pulse=False,
            needs_reconnect=False,
            never_connected=False,
            last_connected_at=None,
            last_connected_human=None,
            still_dot=True,
        )
    if readiness is ProviderReadiness.SEEN_NOT_POLLING:
        return ConnectionHealthStatus(
            state=ConnectionHealth.DISCONNECTED,
            label="Not playing yet",
            badge_class="badge-soon",
            pulse=False,
            needs_reconnect=True,
            never_connected=False,
            last_connected_at=None,
            last_connected_human=None,
            still_dot=False,
        )
    return ConnectionHealthStatus(
        state=ConnectionHealth.DISCONNECTED,
        label="No live connection",
        badge_class="badge-alert",
        pulse=False,
        needs_reconnect=True,
        never_connected=True,
        last_connected_at=None,
        last_connected_human=None,
        still_dot=False,
    )


def health_view(status: ConnectionHealthStatus) -> dict[str, object]:
    """Build the health dict the agent templates read from a ``ConnectionHealthStatus``.

    The agent list and detail pages render a plain dict (not the dataclass) with
    the same keys as ``ConnectionHealthStatus``. Both pages synthesize a status
    from the user's coverage-based readiness, then call this to get the dict the
    templates consume, so the two pages can't drift in which keys/values they
    emit.
    """
    return {
        "state": status.state,
        "label": status.label,
        "badge_class": status.badge_class,
        "pulse": status.pulse,
        "still_dot": status.still_dot,
        "needs_reconnect": status.needs_reconnect,
        "never_connected": status.never_connected,
        "last_connected_at": status.last_connected_at,
        "last_connected_human": status.last_connected_human,
        "match_id": status.match_id,
        "game_name": status.game_name,
        "agent_count": status.agent_count,
    }


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None = None
    health: object | None = None
    match_count: int = 0
    provider_label: str | None = None
    connect_url: str | None = None
    needs_connecting: bool = False


@dataclass(frozen=True)
class VersionRow:
    """One row of the agent-detail version timeline: the version, its
    completed-match record, and up-to-3 recent completed-match links."""

    version: AgentVersion
    stats: VersionStats
    frozen: bool
    recent_matches: list[VersionMatchLink] = field(default_factory=list)


@dataclass(frozen=True)
class MatchEntry:
    """One row in the agent-detail matches table."""

    match_id: str
    match_name: str
    game_type: str
    state: GameState
    player_id: int
    round_score: int
    total_score: int
    pre_game: bool


async def _count_agent_matches(db: DbSession, agent_id: int) -> int:
    count = await db.scalar(
        select(func.count()).select_from(Player).where(Player.agent_id == agent_id)
    )
    return int(count or 0)


async def _count_agent_matches_for_agents(
    db: DbSession, agent_ids: list[int]
) -> dict[int, int]:
    if not agent_ids:
        return {}
    rows = (
        await db.execute(
            select(Player.agent_id, func.count().label("match_count"))
            .where(Player.agent_id.in_(agent_ids))
            .group_by(Player.agent_id)
        )
    ).all()
    return {agent_id: int(match_count or 0) for agent_id, match_count in rows}


def _readiness_state(context: dict[str, object]) -> str:
    """Return the onboarding card state for the agent detail page.

    READY is only for live/ready coverage. A provider that is not covered by a
    non-paused connection needs connecting, and a paused agent stays paused so
    the dedicated paused card can keep owning that state.
    """
    health = context.get("health")
    if health is None:
        return "needs_connecting"
    if isinstance(health, dict):
        state = health.get("state")
        needs_reconnect = bool(health.get("needs_reconnect"))
    else:
        state = getattr(health, "state", None)
        needs_reconnect = bool(getattr(health, "needs_reconnect", False))
    if state == ConnectionHealth.PAUSED:
        return "paused"
    if needs_reconnect:
        return "needs_connecting"
    if context.get("join_blocked"):
        return "at_capacity"
    if state in (ConnectionHealth.LIVE, ConnectionHealth.READY):
        return "ready"
    return "needs_connecting"


def _is_ready_to_play(context: dict[str, object]) -> bool:
    """True when the agent can accept a new match invitation right now."""
    return _readiness_state(context) == "ready"
