"""Shared non-left player counts for a match.

One home for the "how many seats are active in this match" query so the
scheduler start-floor, the watchdog, and the arena bot-filler stop re-inlining
the same `select(func.count())` with slightly different seat filters. Leaf
module (imports only the `Player` model, `app.engine.seated`, and
sqlalchemy), so `scheduler.py` and `arena.py` can both use it without an
import cycle. This module is about *counting* seats; the "has this player
left" predicate itself lives in `app.engine.seated` — see that module's
docstring for why.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.seated import seated_filter
from app.models.player import Player


async def active_player_count(
    db: AsyncSession, match_id: str, *, exclude_reserved: bool
) -> int:
    """Count seats in *match_id* that have not left.

    - ``exclude_reserved=True`` → *confirmed* seats only: a held seat
      (join-before-connect, ``seat_reserved_until`` set) is not a real player
      yet, so it is excluded (the start floor and the arena "confirmed" count).
    - ``exclude_reserved=False`` → every not-left seat, including held ones (the
      watchdog "is this game empty?" check and the arena "seated" count).
    """
    conditions = [Player.match_id == match_id, seated_filter()]
    if exclude_reserved:
        conditions.append(Player.seat_reserved_until.is_(None))
    return (
        await db.scalar(select(func.count()).select_from(Player).where(*conditions))
    ) or 0
