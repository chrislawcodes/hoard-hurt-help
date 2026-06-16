"""Idle / no-game detection for the connection-scoped next-turn endpoint.

The MCP "running AI" (Claude / Codex / Gemini following the Mode A play-prompt)
polls ``get_next_turn`` in a loop. When there's nothing to play, the server tells
it apart from two situations:

* **waiting** — the caller is seated in a game that's live (or about to start) but
  no turn is open for it right now. A turn IS coming; keep polling.
* **no_game** — the caller has NO active or upcoming game at all. Nothing is
  coming unless a human joins one. We attach an ``idle_seconds`` count and, once
  the caller has been idle past :data:`IDLE_STOP_SECONDS`, a ``should_stop`` hint
  so an interactive client can stop its loop instead of polling forever.

The idle clock has no dedicated column (we avoid a migration): it's derived from
existing facts — the most recent real move the user's agents submitted, falling
back to when this connection first came online. That anchor only matters in the
no-game case; if a game is live or scheduled we never tell the client to stop.

The hint is advisory. The always-on connector service is meant to run forever and
deliberately ignores ``should_stop`` — only the interactive MCP client acts on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import TurnSubmission

# How long the interactive client may go with no game to play before the server
# hints that it should stop polling. ~10 minutes, per the agreed Mode A play-flow.
IDLE_STOP_SECONDS = 600

# A live game (someone's turn could open any moment) or a scheduled/registering
# game (about to start) both count as "the caller has a game" — never stop then.
_HAS_GAME_STATES = (
    GameState.ACTIVE,
    GameState.SCHEDULED,
    GameState.REGISTERING,
)


@dataclass(frozen=True)
class IdleStatus:
    """Resolved no-game / idle picture for one connection's owner."""

    has_game: bool
    idle_seconds: int
    should_stop: bool
    stop_reason: str | None


async def _user_has_game(db: AsyncSession, user_id: int) -> bool:
    """True if the user has any active AI agent seated in a live or upcoming game."""
    row = (
        await db.execute(
            select(Player.id)
            .join(Agent, Agent.id == Player.agent_id)
            .join(Match, Match.id == Player.match_id)
            .where(
                Agent.user_id == user_id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
                Player.left_at.is_(None),
                Match.state.in_(_HAS_GAME_STATES),
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _last_activity_at(
    db: AsyncSession, connection: Connection
) -> datetime:
    """When the connection's owner last had game activity.

    The most recent real (non-defaulted) move by any of the user's agents is the
    truest "was playing" signal. With no moves yet, fall back to when this
    connection came online so a freshly-connected, never-played caller still gets
    the full idle window before being told to stop.
    """
    last_submit = (
        await db.execute(
            select(TurnSubmission.submitted_at)
            .join(Player, Player.id == TurnSubmission.player_id)
            .join(Agent, Agent.id == Player.agent_id)
            .where(
                Agent.user_id == connection.user_id,
                TurnSubmission.was_defaulted.is_(False),
                TurnSubmission.submitted_at.is_not(None),
            )
            .order_by(TurnSubmission.submitted_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_submit is not None:
        return ensure_aware(last_submit)
    fallback = (
        connection.mode_a_at
        or connection.first_connected_at
        or connection.created_at
    )
    return ensure_aware(fallback)


async def compute_idle_status(
    db: AsyncSession, connection: Connection, *, now: datetime | None = None
) -> IdleStatus:
    """Resolve whether the caller has a game and, if not, how long it's been idle.

    ``should_stop`` is only ever True when there's NO game and the idle window has
    elapsed — a client with a game waiting (or one that recently played) is never
    told to stop.
    """
    now = now or datetime.now(timezone.utc)
    if await _user_has_game(db, connection.user_id):
        return IdleStatus(
            has_game=True, idle_seconds=0, should_stop=False, stop_reason=None
        )
    anchor = await _last_activity_at(db, connection)
    idle_seconds = max(0, int((now - anchor).total_seconds()))
    should_stop = idle_seconds >= IDLE_STOP_SECONDS
    return IdleStatus(
        has_game=False,
        idle_seconds=idle_seconds,
        should_stop=should_stop,
        stop_reason="idle_timeout" if should_stop else None,
    )
