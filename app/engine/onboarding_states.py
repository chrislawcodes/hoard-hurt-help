"""Shared onboarding primitives across the agent/connection onboarding modules.

`connection_activity`, `agent_onboarding`, and `agent_idle` all need the same two
things — the "pregame" game states and a "has this agent actually moved?" check —
so they live here once instead of being re-defined in each. Leaf module (imports
only models + sqlalchemy), so all three can import it without a cycle. The two
onboarding *state machines* (their enums + precedence) deliberately stay in their
own modules; only these shared primitives are unified.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import GameState
from app.models.player import Player
from app.models.turn import TurnSubmission

# A game that has not started yet (waiting to fill / scheduled).
PREGAME_STATES = (GameState.SCHEDULED, GameState.REGISTERING)


async def has_moved(db: AsyncSession, agent_id: int) -> bool:
    """True if the agent has at least one real (non-defaulted) TurnSubmission."""
    stmt = (
        select(TurnSubmission.id)
        .join(Player, Player.id == TurnSubmission.player_id)
        .where(Player.agent_id == agent_id, TurnSubmission.was_defaulted.is_(False))
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None
