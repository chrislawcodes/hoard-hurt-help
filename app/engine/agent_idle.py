"""Idle / no-game detection and poll pacing for the connection-scoped next-turn
endpoint.

The "running AI" (an MCP client following the play prompt, or the always-on
connector) polls ``get_next_turn`` in a loop. The server decides how
soon it should ask again — the AI just obeys the number. Every "ask" by an
interactive client is a paid model think, so the goal is: ask as rarely as possible
without missing a turn.

Two regimes, paced off the SOONEST game the caller is seated in:

* **In a live game** — a turn could open any second. The server *long-polls*:
  it holds the request open (cheap — no model thinking while it waits) and answers
  the instant a turn opens. After a hold with nothing, ask again in seconds.
* **Before a game** — no turn exists yet, so there's nothing to hold open for.
  The server hands back a plain "wait N seconds": ~5 minutes when a start is far
  off (or there's no game at all), tightening to ~1 minute inside the last five,
  then switching to a long-poll in the final minute so the AI is already waiting
  when the first turn (with its 60-second deadline) opens.

Naps are capped so a long nap can never carry the AI *past* the next, tighter lane
— it can be a little early, never late. And the speed always follows the soonest
game, so joining a far game then a near one pulls the cadence tight for the near
one.

The idle clock (for the no-game case) has no dedicated column: it's derived from
the most recent real move the user's agents made, falling back to when the
connection came online. ``should_stop`` only ever fires when there is NO game at
all and that clock passes :data:`IDLE_STOP_SECONDS`; a caller with any game
scheduled or live is never told to stop. The always-on connector ignores
``should_stop`` and runs forever by design — only the interactive client acts on
it.
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

# How long the interactive client may go with NO game before the server hints it
# should stop polling. ~10 minutes.
IDLE_STOP_SECONDS = 600

# Long-poll: hold the request open this long, watching for a turn to open. Kept
# well under common proxy timeouts (~100s) so the held request always returns
# cleanly rather than being cut by an intermediary.
LONG_POLL_HOLD_SECONDS = 40
# How often, inside a hold, to re-check the DB for a freshly opened turn.
# 5s is plenty: turns carry a 60-second deadline, so worst-case detection lag
# is 5s. Tighter intervals multiply DB load across concurrent MCP sessions.
LONG_POLL_INTERVAL_SECONDS = 5.0

# In a live game (or the final approach to a start), after a hold returns nothing,
# ask again this soon — a turn can open any moment.
POLL_IN_PLAY_SECONDS = 5
# Begin long-polling this many seconds before a scheduled start, so the AI is
# already holding the line open when the first turn opens.
LONG_POLL_LEAD_SECONDS = 60
# Inside this window before a start, check every minute.
NEAR_START_WINDOW_SECONDS = 300
POLL_NEAR_START_SECONDS = 60
# Far from any start, or no game at all: check every five minutes.
POLL_WAITING_SECONDS = 300

# A live game (a turn could open any moment) or a scheduled/registering game
# (about to start) both count as "the caller has a game" — never stop then.
_HAS_GAME_STATES = (
    GameState.ACTIVE,
    GameState.SCHEDULED,
    GameState.REGISTERING,
)
_UPCOMING_STATES = (GameState.SCHEDULED, GameState.REGISTERING)


@dataclass(frozen=True)
class GameTiming:
    """What games the user has right now and how soon the soonest one starts.

    The raw facts behind both the loop's pacing and the on-page status line — so
    the two can never disagree about "you have a game / it starts in N seconds."
    """

    has_game: bool
    # Any seated game in the ACTIVE state — a turn could open any second.
    has_live_game: bool
    # Seconds until the soonest scheduled/registering game starts, floored at 0.
    # ``None`` when no upcoming game is scheduled (no game, or only live games).
    seconds_to_next_start: int | None


@dataclass(frozen=True)
class IdleStatus:
    """Resolved no-game / idle / soonest-start picture for one connection's owner."""

    has_game: bool
    # Any seated game in the ACTIVE state — a turn could open any second.
    has_live_game: bool
    # Seconds until the soonest scheduled/registering game starts, floored at 0.
    # ``None`` when no upcoming game is scheduled (no game, or only live games).
    seconds_to_next_start: int | None
    idle_seconds: int
    should_stop: bool
    stop_reason: str | None


def pace_idle(idle: IdleStatus) -> tuple[float, int]:
    """Decide ``(long_poll_hold_seconds, next_poll_after_seconds)`` for a poll that
    has no turn to serve right now.

    All timing lives here, in one place — the AI never reasons about start times;
    it just obeys ``next_poll_after_seconds`` and waits out the hold. Naps are
    capped so they can't overshoot into a looser lane or past a start.
    """
    # Live game: a turn could open any second. Hold the line; ask again soon.
    if idle.has_live_game:
        return (float(LONG_POLL_HOLD_SECONDS), POLL_IN_PLAY_SECONDS)

    seconds = idle.seconds_to_next_start
    # Nothing scheduled at all → cheap waiting (and maybe ``should_stop``).
    if seconds is None:
        return (0.0, POLL_WAITING_SECONDS)

    # Final approach: hold the line so we catch the opening turn the instant it
    # opens — its 60-second deadline leaves no room to be a nap late.
    if seconds <= LONG_POLL_LEAD_SECONDS:
        return (float(LONG_POLL_HOLD_SECONDS), POLL_IN_PLAY_SECONDS)

    # Inside the last five minutes: check every minute, but never nap past the
    # long-poll lead window.
    if seconds <= NEAR_START_WINDOW_SECONDS:
        nap = min(POLL_NEAR_START_SECONDS, seconds - LONG_POLL_LEAD_SECONDS)
        return (0.0, max(POLL_IN_PLAY_SECONDS, nap))

    # Far off: check every five minutes, but never nap past the near-start window.
    nap = min(POLL_WAITING_SECONDS, seconds - NEAR_START_WINDOW_SECONDS)
    return (0.0, max(POLL_NEAR_START_SECONDS, nap))


async def _seated_game_states(
    db: AsyncSession, user_id: int, *, agent_id: int | None = None
) -> list[tuple[GameState, datetime | None]]:
    """The (state, scheduled_start) of every live-or-upcoming game the user's
    active AI agents are seated in.

    When ``agent_id`` is given, restrict to that one agent — so a per-agent loop
    paces off ITS own soonest game, not the whole connection's busiest agent.
    """
    stmt = (
        select(Match.state, Match.scheduled_start)
        .join(Player, Player.match_id == Match.id)
        .join(Agent, Agent.id == Player.agent_id)
        .where(
            Agent.user_id == user_id,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state.in_(_HAS_GAME_STATES),
        )
    )
    if agent_id is not None:
        stmt = stmt.where(Agent.id == agent_id)
    rows = (await db.execute(stmt)).all()
    return [(state, start) for state, start in rows]


async def game_timing_for_user(
    db: AsyncSession,
    user_id: int,
    *,
    now: datetime | None = None,
    agent_id: int | None = None,
) -> GameTiming:
    """The user's live-or-upcoming game picture: do they have a game, is one live,
    and how soon does the soonest scheduled one start.

    Shared by the play loop's pacing and the on-page "next game" status line, so
    both read the same truth. Pass ``agent_id`` to scope to one agent.
    """
    now = now or datetime.now(timezone.utc)
    games = await _seated_game_states(db, user_id, agent_id=agent_id)
    has_live = any(state == GameState.ACTIVE for state, _ in games)
    starts = [
        max(0, int((ensure_aware(start) - now).total_seconds()))
        for state, start in games
        if state in _UPCOMING_STATES and start is not None
    ]
    return GameTiming(
        has_game=bool(games),
        has_live_game=has_live,
        seconds_to_next_start=min(starts) if starts else None,
    )


async def _last_activity_at(db: AsyncSession, connection: Connection) -> datetime:
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
        connection.mcp_connected_at
        or connection.first_connected_at
        or connection.created_at
    )
    return ensure_aware(fallback)


async def compute_idle_status(
    db: AsyncSession,
    connection: Connection,
    *,
    now: datetime | None = None,
    agent_id: int | None = None,
) -> IdleStatus:
    """Resolve whether the caller has a game, how soon the soonest one starts, and
    — when there's no game — how long it's been idle.

    ``should_stop`` is only ever True when there's NO game and the idle window has
    elapsed; a client with a game live or scheduled is never told to stop.

    When ``agent_id`` is given (a per-agent loop), the game picture is scoped to
    that agent, so its pacing follows its own soonest game — not a busier sibling
    agent on the same connection.
    """
    now = now or datetime.now(timezone.utc)
    timing = await game_timing_for_user(
        db, connection.user_id, now=now, agent_id=agent_id
    )
    if timing.has_game:
        return IdleStatus(
            has_game=True,
            has_live_game=timing.has_live_game,
            seconds_to_next_start=timing.seconds_to_next_start,
            idle_seconds=0,
            should_stop=False,
            stop_reason=None,
        )
    anchor = await _last_activity_at(db, connection)
    idle_seconds = max(0, int((now - anchor).total_seconds()))
    should_stop = idle_seconds >= IDLE_STOP_SECONDS
    return IdleStatus(
        has_game=False,
        has_live_game=False,
        seconds_to_next_start=None,
        idle_seconds=idle_seconds,
        should_stop=should_stop,
        stop_reason="idle_timeout" if should_stop else None,
    )
