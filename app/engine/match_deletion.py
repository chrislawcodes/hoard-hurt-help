"""Shared match teardown: delete cascade and cancel transition."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.scheduler import registry
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.turn import Turn, TurnMessage, TurnSubmission


async def cancel_match(db: AsyncSession, match: Match) -> None:
    """Cancel a match: stop its scheduler task and mark it CANCELLED.

    Preserves all match data (unlike ``delete_match``). The caller owns the
    allowed-state policy — this only performs the transition. ``registry.stop``
    is a no-op when the match has no running task (e.g. a pre-start match).
    """
    registry.stop(match.id)
    match.state = GameState.CANCELLED
    match.cancelled_at = datetime.now(timezone.utc)
    await db.commit()


async def delete_match(db: AsyncSession, match_id: str) -> None:
    """Stop a match and delete dependent rows in the safe order."""
    # Stop the scheduler task before touching rows. The asyncio task is
    # cooperative, so it may write one more TurnSubmission before the
    # CancelledError fires. The second pass below (by player_id) catches that.
    registry.stop(match_id)
    turn_ids = select(Turn.id).where(Turn.match_id == match_id)
    await db.execute(delete(TurnSubmission).where(TurnSubmission.turn_id.in_(turn_ids)))
    await db.execute(delete(TurnMessage).where(TurnMessage.turn_id.in_(turn_ids)))
    await db.execute(delete(Turn).where(Turn.match_id == match_id))
    # Second pass: catch any submissions written by the scheduler in the
    # cancellation window before it yielded. Also clears winner_player_id so
    # the Match->Player FK doesn't block the Player delete.
    player_ids = select(Player.id).where(Player.match_id == match_id)
    await db.execute(delete(TurnSubmission).where(TurnSubmission.player_id.in_(player_ids)))
    await db.execute(delete(TurnMessage).where(TurnMessage.player_id.in_(player_ids)))
    await db.execute(
        update(Match).where(Match.id == match_id).values(winner_player_id=None)
    )
    await db.execute(delete(Player).where(Player.match_id == match_id))
    await db.execute(delete(RequestIncident).where(RequestIncident.match_id == match_id))
    await db.execute(delete(Match).where(Match.id == match_id))
    await db.commit()
