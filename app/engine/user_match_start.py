"""User-initiated match start: let the only player kick off their own match.

A match sits in REGISTERING until its scheduled time, then the poller starts it
only if it has at least ``MIN_PLAYERS_TO_START`` players. That leaves a gap:
someone who is the only player — whether they created the match or joined an
auto-scheduled one — must wait for the clock, and risks an auto-cancel if no
strangers show up.

This module fills that gap. When the signed-in viewer is the *only* person with
a human/agent seat in the match (bots don't count), they may start it now,
whatever the match kind. Any empty seats below the start floor fill with bots so
the match can actually run, mirroring how Practice Arena and auto-matches already
seat bots.

The eligibility check is shared by the viewer (to decide whether to show the
"Start now" button) and the start route (to authorize the POST), so the button
and the action can't drift.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User


@dataclass(frozen=True)
class StartEligibility:
    """Whether a viewer may start a match now, and how many bots that needs."""

    can_start: bool
    bots_to_add: int


_CANNOT_START = StartEligibility(can_start=False, bots_to_add=0)


def _is_bot(kind: object) -> bool:
    """True for a scripted bot seat (enum member or its raw string value)."""
    return kind in (AgentKind.BOT, AgentKind.BOT.value)


async def viewer_start_eligibility(
    db: AsyncSession, match: Match, user: User | None
) -> StartEligibility:
    """Can ``user`` start ``match`` now? Returns the verdict + bot-fill count.

    Eligible only when ALL of these hold:

    * the viewer is signed in and the match is still pre-start
      (SCHEDULED/REGISTERING) — any match kind, including an auto-scheduled one,
    * the viewer holds at least one *confirmed* (not held, not left) human or
      agent seat, and
    * no other user holds a human or agent seat — bots don't count, so a table
      that is just you plus bots still reads as solo.

    ``bots_to_add`` is how many bots starting now would seat to reach the start
    floor (0 if you already have enough players). If the table is too full to
    reach the floor even after filling bots, the verdict is "can't start".
    """
    # Imported here (not at module load) to avoid a cycle: scheduler imports
    # arena, which would otherwise pull this module in transitively.
    from app.engine.scheduler import MIN_PLAYERS_TO_START

    if user is None:
        return _CANNOT_START
    if match.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        return _CANNOT_START

    rows = (
        await db.execute(
            select(Player.user_id, Player.seat_reserved_until, Agent.kind)
            .join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id == match.id, Player.left_at.is_(None))
        )
    ).all()

    human_or_agent = [r for r in rows if not _is_bot(r.kind)]
    if any(r.user_id != user.id for r in human_or_agent):
        return _CANNOT_START  # someone else is in — not a solo match
    # The viewer needs a live (confirmed, not held) seat of their own. A held
    # seat — its chosen AI isn't online yet — is dropped at start, so it can't be
    # the seat you start the match on.
    viewer_is_live = any(
        r.user_id == user.id and r.seat_reserved_until is None for r in human_or_agent
    )
    if not viewer_is_live:
        return _CANNOT_START

    # Confirmed seats (bots included) are what count toward the start floor; a
    # held seat occupies the table but isn't a real player yet.
    confirmed = sum(1 for r in rows if r.seat_reserved_until is None)
    room = max(0, match.max_players - len(rows))
    bots_to_add = min(max(0, MIN_PLAYERS_TO_START - confirmed), room)
    if confirmed + bots_to_add < MIN_PLAYERS_TO_START:
        return _CANNOT_START  # can't reach the floor even after filling bots
    return StartEligibility(can_start=True, bots_to_add=bots_to_add)


async def start_match_for_user(db: AsyncSession, match: Match) -> None:
    """Fill empty seats with bots up to the floor, then start the match.

    The caller must have authorized via :func:`viewer_start_eligibility`. Safe
    against a concurrent start (the background poller): it re-reads state and
    no-ops if the match already left the pre-start states.
    """
    from app.engine.arena import fill_match_with_bots
    from app.engine.scheduler import MIN_PLAYERS_TO_START, start_game

    await db.refresh(match)
    if match.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        return  # already started or cancelled elsewhere — nothing to do
    await fill_match_with_bots(db, match, MIN_PLAYERS_TO_START)
    await start_game(db, match)
