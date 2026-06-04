"""Managed match lifecycle for Practice Arena and Auto-Scheduled matches.

Two match types are always available to operators without admin intervention:

* **Practice Arena** — one open match, pre-seeded with Sims, that starts the
  instant any human joins. Immediately replaced when the match ends.
* **Auto-Match** — one open match per 30-minute clock boundary. At its
  scheduled start time, Sims fill empty seats and the match runs regardless of
  human count.

All public functions are idempotent — safe to call every 2 seconds from the
background poller without creating duplicates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func

from app.engine.sim_presets import sim_presets
from app.engine.sims.seating import SimSeatingError, add_sims_to_game
from app.engine.tokens import generate_match_id
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player

logger = logging.getLogger(__name__)

PRACTICE_ARENA_NAME = "Practice Arena"
PRACTICE_ARENA_MAX_PLAYERS = 5
PRACTICE_ARENA_SIM_COUNT = 4

AUTO_MATCH_INTERVAL_MINUTES = 30
AUTO_MATCH_MAX_PLAYERS = 8
AUTO_MATCH_SIM_COUNT_MAX = 7
AUTO_MATCH_NAME_PREFIX = "Auto Match"


def _choose_sim_seats(n: int) -> list[tuple[str, str]]:
    """Pick n sim personality IDs from the preset list (cycling if needed)."""
    presets = sim_presets()
    if not presets:
        return []
    seats = []
    for i in range(n):
        preset = presets[i % len(presets)]
        seats.append((f"Sim_{i + 1}", preset.id))
    return seats


async def _next_match_id(db: AsyncSession) -> str:
    existing_ids = (await db.execute(select(Match.id))).scalars().all()

    def _numeric_suffix(match_id: str) -> int | None:
        parts = match_id.split("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return int(parts[1])
        return None

    n = max(
        (s for x in existing_ids if x.startswith("M_") and (s := _numeric_suffix(x)) is not None),
        default=0,
    ) + 1
    return generate_match_id(n)


def _next_boundary() -> datetime:
    """Return the next :00 or :30 UTC boundary from now."""
    now = datetime.now(timezone.utc)
    minute = now.minute
    if minute < 30:
        candidate = now.replace(minute=30, second=0, microsecond=0)
    else:
        candidate = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return candidate


async def ensure_practice_arena(db: AsyncSession) -> None:
    """Create a Practice Arena if none is open. Idempotent."""
    existing = (
        await db.execute(
            select(Match).where(
                Match.match_kind == MatchKind.PRACTICE_ARENA.value,
                Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
            )
        )
    ).scalars().first()
    if existing is not None:
        return

    presets = sim_presets()
    if not presets:
        logger.warning("No Sim presets available — Practice Arena not created.")
        return

    match_id = await _next_match_id(db)
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    arena = Match(
        id=match_id,
        name=PRACTICE_ARENA_NAME,
        state=GameState.REGISTERING,
        scheduled_start=far_future,
        min_players=1,
        max_players=PRACTICE_ARENA_MAX_PLAYERS,
        match_kind=MatchKind.PRACTICE_ARENA.value,
    )
    db.add(arena)
    await db.flush()

    seats = _choose_sim_seats(PRACTICE_ARENA_SIM_COUNT)
    try:
        await add_sims_to_game(db, arena, seats)
    except SimSeatingError as exc:
        logger.exception("Failed to seat Sims in Practice Arena: %s", exc)
        await db.rollback()
        return

    logger.info("Created Practice Arena %s with %d Sim seats.", match_id, len(seats))


async def ensure_auto_match(db: AsyncSession) -> None:
    """Create the next auto-match window if none is open. Idempotent."""
    now = datetime.now(timezone.utc)
    existing = (
        await db.execute(
            select(Match).where(
                Match.match_kind == MatchKind.AUTO_SCHEDULED.value,
                Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                Match.scheduled_start >= now,
            )
        )
    ).scalars().first()
    if existing is not None:
        return

    boundary = _next_boundary()
    match_id = await _next_match_id(db)
    name = f"{AUTO_MATCH_NAME_PREFIX} {boundary.strftime('%H:%M')}"
    auto = Match(
        id=match_id,
        name=name,
        state=GameState.SCHEDULED,
        scheduled_start=boundary,
        min_players=1,
        max_players=AUTO_MATCH_MAX_PLAYERS,
        match_kind=MatchKind.AUTO_SCHEDULED.value,
    )
    db.add(auto)
    await db.commit()
    logger.info("Created auto-match %s scheduled at %s.", match_id, boundary.isoformat())


async def fill_and_start_auto_matches(db: AsyncSession) -> None:
    """Fill overdue auto-matches with Sims and start them."""
    # Late import to avoid circular dependency: scheduler imports arena.
    from app.engine.scheduler import start_game

    now = datetime.now(timezone.utc)
    due = (
        await db.execute(
            select(Match).where(
                Match.match_kind == MatchKind.AUTO_SCHEDULED.value,
                Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                Match.scheduled_start <= now,
            )
        )
    ).scalars().all()

    for match in due:
        player_count = (
            await db.scalar(
                select(func.count())
                .select_from(Player)
                .where(Player.match_id == match.id, Player.left_at.is_(None))
            )
        ) or 0
        empty_slots = match.max_players - player_count
        if empty_slots > 0:
            n_sims = min(empty_slots, AUTO_MATCH_SIM_COUNT_MAX)
            seats = _choose_sim_seats(n_sims)
            if seats:
                try:
                    await add_sims_to_game(db, match, seats)
                except SimSeatingError as exc:
                    logger.exception(
                        "Failed to seat Sims in auto-match %s: %s", match.id, exc
                    )
                    continue

        await start_game(db, match)
        logger.info("Started auto-match %s.", match.id)
