"""Seat-hold logic for join-before-connect.

When a user joins a match with an agent whose AI provider isn't live yet, the
seat is *held*: ``players.seat_reserved_until`` is set to a deadline. The user
has ``SEAT_HOLD_SECONDS`` to bring the provider online. This module:

- confirms a held seat (clears the deadline) the moment its provider goes live,
- releases (deletes) a held seat whose deadline has passed before it went live,
- releases all still-held seats when a match starts.

A held seat never counts as a real player — see ``_active_player_count`` in the
scheduler, which excludes rows with a non-NULL ``seat_reserved_until``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.aware_datetime import ensure_aware
from app.db import SessionLocal
from app.engine.connection_health import (
    ProviderReadiness,
    provider_readiness,
    user_play_readiness,
)
from app.models.connection import ConnectionProvider
from app.models.player import Player

# How long a held seat is kept while the user brings their AI online. This is a
# generous window, not a race: first-time setup (add the MCP server + sign in)
# easily takes minutes, so a short timer just evicts people mid-setup. Held seats
# are released the moment the match actually starts (see release_held_seats), so
# a long hold never blocks a game from beginning — it only avoids punishing a
# user who is still connecting. The connect screens no longer show a countdown.
SEAT_HOLD_SECONDS = 15 * 60  # 15 minutes


def hold_deadline(now: datetime) -> datetime:
    """The deadline for a seat held starting at *now*."""
    return now + timedelta(seconds=SEAT_HOLD_SECONDS)


async def confirm_seat_if_live(db: AsyncSession, player: Player) -> bool:
    """Clear the hold on *player* once the AI it was joined with is running.

    Keys off the play-loop heartbeat (``ProviderReadiness.LIVE``) for the seat's
    *chosen* provider — so the seat confirms only when the AI the user actually
    picked starts playing, the same bar the join gate uses. Legacy seats with no
    chosen provider fall back to "any live connection". Returns True when the seat
    was confirmed. Does not commit — the caller owns the transaction.
    """
    if player.seat_reserved_until is None:
        return False
    if player.chosen_provider:
        readiness = await provider_readiness(
            db, player.user_id, ConnectionProvider(player.chosen_provider)
        )
    else:
        readiness = await user_play_readiness(db, player.user_id)
    if readiness == ProviderReadiness.LIVE:
        player.seat_reserved_until = None
        return True
    return False


async def release_held_seats(db: AsyncSession, match_id: str) -> None:
    """Delete every still-held seat in *match_id* (used at match start).

    Does not commit — the caller owns the transaction.
    """
    held = list(
        (
            await db.execute(
                select(Player).where(
                    Player.match_id == match_id,
                    Player.seat_reserved_until.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for player in held:
        await db.delete(player)


async def sweep_held_seats(session_factory: async_sessionmaker | None = None) -> None:
    """Poller subsystem: confirm held seats that went live; release expired ones.

    Runs each poll tick. For every held seat (deadline set, not yet left):
    confirm it if its provider is now live, otherwise delete it once its
    deadline has passed.
    """
    factory = session_factory or SessionLocal
    async with factory() as db:
        now = datetime.now(timezone.utc)
        held = list(
            (
                await db.execute(
                    select(Player).where(
                        Player.seat_reserved_until.is_not(None),
                        Player.left_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        changed = False
        for player in held:
            if await confirm_seat_if_live(db, player):
                changed = True
                continue
            deadline = player.seat_reserved_until
            if deadline is not None and ensure_aware(deadline) <= now:
                await db.delete(player)
                changed = True
        if changed:
            await db.commit()
