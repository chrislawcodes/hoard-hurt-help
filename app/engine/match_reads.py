"""Shared read queries over a match's players and per-turn rows.

The lobby, the admin dashboard, and the spectator/viewer assembly each need to
count a match's players or pull its talk/act rows grouped by turn. Keeping these
queries in one place stops each route from re-issuing the same SQL with subtly
different ordering.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.player import Player
from app.models.turn import TurnMessage, TurnSubmission


async def player_count(db: AsyncSession, match_id: str, *, active_only: bool = True) -> int:
    """Count players in a match.

    With ``active_only`` (the default) a bot that has left frees its seat and is
    not counted; pass ``active_only=False`` to count every player who ever joined.
    """
    stmt = select(func.count()).select_from(Player).where(Player.match_id == match_id)
    if active_only:
        stmt = stmt.where(Player.left_at.is_(None))
    return int((await db.execute(stmt)).scalar_one())


async def load_messages_by_turn(
    db: AsyncSession, turn_ids: Sequence[int]
) -> dict[int, list[TurnMessage]]:
    """Talk-phase messages for the given turns, grouped by turn_id in send order."""
    grouped: dict[int, list[TurnMessage]] = {}
    if not turn_ids:
        return grouped
    rows = (
        (
            await db.execute(
                select(TurnMessage)
                .where(TurnMessage.turn_id.in_(turn_ids))
                .order_by(TurnMessage.turn_id, TurnMessage.submitted_at, TurnMessage.id)
            )
        )
        .scalars()
        .all()
    )
    for message in rows:
        grouped.setdefault(message.turn_id, []).append(message)
    return grouped


async def load_submissions_by_turn(
    db: AsyncSession, turn_ids: Sequence[int]
) -> dict[int, list[TurnSubmission]]:
    """Act-phase submissions for the given turns, grouped by turn_id in submit order."""
    grouped: dict[int, list[TurnSubmission]] = {}
    if not turn_ids:
        return grouped
    rows = (
        (
            await db.execute(
                select(TurnSubmission)
                .where(TurnSubmission.turn_id.in_(turn_ids))
                .order_by(
                    TurnSubmission.turn_id,
                    TurnSubmission.submitted_at,
                    TurnSubmission.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for sub in rows:
        grouped.setdefault(sub.turn_id, []).append(sub)
    return grouped
