"""Onboarding-state resolution for AI agents.

Mirrors the five-state narration from the old bot detail page, adapted for the
new Connection-based agent model:

  1. waiting           — runner never connected (connection.first_connected_at is None),
                         not enrolled in any match
  2. connected_no_game — connected at least once, idle (no active / pre-game match)
  3. connected_pregame — connected, seated in a match that hasn't started yet
  4. in_game_no_move   — connected, in an active match, but no real submission yet
  5. playing           — at least one non-defaulted TurnSubmission exists

"Connected" is determined by Connection.first_connected_at, not last_seen_at —
even a cold runner counts as having connected if it reached us at least once.

"Has moved" is derived from TurnSubmission.was_defaulted=False, the same query
used by mark_first_move in connection_activity.py.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import GameState
from app.models.player import Player
from app.models.turn import TurnSubmission

# Pre-game states the engine uses
_PREGAME_STATES = (GameState.SCHEDULED, GameState.REGISTERING)


class AgentOnboardingState(str, enum.Enum):
    """Where an agent sits on the connect → playing path."""

    WAITING = "waiting"  # runner never connected, not in a match
    CONNECTED_NO_GAME = "connected_no_game"  # connected, no active/pregame match
    CONNECTED_PREGAME = "connected_pregame"  # connected, in a match not yet started
    IN_GAME_NO_MOVE = "in_game_no_move"  # active match, no real move yet
    PLAYING = "playing"  # has made at least one real (non-defaulted) move


@dataclass(frozen=True)
class AgentOnboardingStatus:
    """Resolved onboarding state plus enough context to render the UI card."""

    state: AgentOnboardingState
    # Present when the agent is in a match (states 3, 4, 5):
    match_id: str | None = None
    match_name: str | None = None
    game_type: str | None = None


@runtime_checkable
class _MatchEntryLike(Protocol):
    """Protocol covering both MatchEntry (from _load_agent_matches) and raw Match ORM rows."""

    @property
    def match_id(self) -> str: ...

    @property
    def match_name(self) -> str: ...

    @property
    def game_type(self) -> str: ...

    @property
    def state(self) -> GameState: ...


@runtime_checkable
class _MatchOrmLike(Protocol):
    """Protocol covering raw Match ORM objects."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def game(self) -> str: ...

    @property
    def state(self) -> GameState: ...


def _mid(m: object) -> str:
    """Return the match ID from either a MatchEntry or a raw Match ORM row."""
    if isinstance(m, _MatchEntryLike):
        return m.match_id
    if isinstance(m, _MatchOrmLike):
        return m.id
    return ""


def _mname(m: object) -> str:
    """Return the match name from either a MatchEntry or a raw Match ORM row."""
    if isinstance(m, _MatchEntryLike):
        return m.match_name
    if isinstance(m, _MatchOrmLike):
        return m.name
    return ""


def _gtype(m: object) -> str:
    """Return the game type from either a MatchEntry or a raw Match ORM row."""
    if isinstance(m, _MatchEntryLike):
        return m.game_type
    if isinstance(m, _MatchOrmLike):
        return m.game
    return ""


def _mstate(m: object) -> GameState | None:
    """Return the state field from a match-like object."""
    state = getattr(m, "state", None)
    if isinstance(state, GameState):
        return state
    return None


async def _has_moved(db: AsyncSession, agent_id: int) -> bool:
    """True if the agent has at least one non-defaulted TurnSubmission."""
    row = (
        await db.execute(
            select(TurnSubmission.id)
            .join(Player, Player.id == TurnSubmission.player_id)
            .where(
                Player.agent_id == agent_id,
                TurnSubmission.was_defaulted.is_(False),
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def compute_agent_onboarding_state(
    db: AsyncSession,
    agent_id: int,
    first_connected_at: object,
    matches: list[object],
) -> AgentOnboardingStatus:
    """Resolve the agent's onboarding state.

    Parameters
    ----------
    db:
        Async DB session.
    agent_id:
        The agent's primary key.
    first_connected_at:
        Value of ``Connection.first_connected_at`` for the agent's connection,
        or ``None`` if the agent has no connection or the runner has never
        connected.
    matches:
        Pre-loaded list of match objects (or MatchEntry rows) with at minimum
        ``.state``, ``.match_id`` / ``.id``, ``.match_name`` / ``.name``,
        and ``.game_type`` / ``.game`` attributes.  Pass the output of
        ``_load_agent_matches`` directly; this function never re-queries
        matches to stay cheap for the polled fragment.

    Precedence (first match wins): playing → in_game_no_move →
    connected_pregame → connected_no_game → waiting.
    """
    connected = first_connected_at is not None

    # Find the most relevant match for the card to point at.
    active_match: object | None = next(
        (m for m in matches if _mstate(m) == GameState.ACTIVE), None
    )
    pregame_match: object | None = next(
        (m for m in matches if _mstate(m) in _PREGAME_STATES), None
    )

    # 5. Playing — any real move exists (takes precedence over match state so
    #    established agents always resolve correctly even if cold right now).
    if await _has_moved(db, agent_id):
        return AgentOnboardingStatus(
            state=AgentOnboardingState.PLAYING,
            match_id=_mid(active_match) if active_match else None,
            match_name=_mname(active_match) if active_match else None,
            game_type=_gtype(active_match) if active_match else None,
        )

    if connected:
        # 4. Connected, in an active match, but no move yet.
        if active_match is not None:
            return AgentOnboardingStatus(
                state=AgentOnboardingState.IN_GAME_NO_MOVE,
                match_id=_mid(active_match),
                match_name=_mname(active_match),
                game_type=_gtype(active_match),
            )
        # 3. Connected, seated in a match that hasn't started.
        if pregame_match is not None:
            return AgentOnboardingStatus(
                state=AgentOnboardingState.CONNECTED_PREGAME,
                match_id=_mid(pregame_match),
                match_name=_mname(pregame_match),
                game_type=_gtype(pregame_match),
            )
        # 2. Connected, idle.
        return AgentOnboardingStatus(state=AgentOnboardingState.CONNECTED_NO_GAME)

    # 1. Waiting — never connected (detail.html already shows a "Runner hasn't
    #    connected yet" card for this, so the onboarding fragment stays blank).
    return AgentOnboardingStatus(state=AgentOnboardingState.WAITING)
