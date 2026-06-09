"""Managed match lifecycle for Practice Arena and Auto-Scheduled matches.

Two match types are always available to operators without admin intervention:

* **Practice Arena** — one open match, pre-seeded with bots, that starts the
  instant any human joins. Immediately replaced when the match ends.
* **Auto-Match** — one open match per 30-minute clock boundary. At its
  scheduled start time, it runs only if at least one external agent joined.
  Bots can fill remaining seats once a real participant is present.

All public functions are idempotent — safe to call every 2 seconds from the
background poller without creating duplicates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.sim_presets import SIM_PRESETS, allocate_default_sim_names, sim_presets
from app.engine.sims.seating import SimSeatingError, add_bots_to_game
from app.engine.tokens import generate_match_id
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player

logger = logging.getLogger(__name__)

PRACTICE_ARENA_NAME = "Practice Arena"
PRACTICE_ARENA_MAX_PLAYERS = 10
PRACTICE_ARENA_SIM_COUNT = len(SIM_PRESETS)  # one bot per default strategy
PRACTICE_ARENA_TOTAL_ROUNDS = 7
PRACTICE_ARENA_TURNS_PER_ROUND = 7

AUTO_MATCH_INTERVAL_MINUTES = 30
AUTO_MATCH_MAX_PLAYERS = 8
AUTO_MATCH_SIM_COUNT_MAX = 7
AUTO_MATCH_TOTAL_ROUNDS = 7
AUTO_MATCH_TURNS_PER_ROUND = 7

# Rotating names for auto-matches — one name per 30-min boundary slot (48/day),
# cycling through the list.  Keyed deterministically by slot index so the same
# boundary always gets the same name across restarts.
_AUTO_MATCH_NAMES: tuple[str, ...] = (
    "Iron Accord",
    "Silver Pact",
    "Crimson Summit",
    "Shadow Council",
    "Storm Table",
    "Ember Trial",
    "Hollow Gambit",
    "Gilded Forum",
    "Jade Alliance",
    "Frost Round",
    "Amber Summit",
    "Obsidian Accord",
    "Scarlet Council",
    "Void Gambit",
    "Copper Pact",
    "Onyx Trial",
)


def _choose_bot_seats(
    n: int,
    *,
    used_names: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Pick n bot personality IDs from the preset list (cycling if needed)."""
    presets = sim_presets()
    if not presets:
        return []
    names = allocate_default_sim_names(n, used_names=used_names)
    seats = []
    for i in range(n):
        preset = presets[i % len(presets)]
        seats.append((names[i], preset.id))
    return seats


_choose_sim_seats = _choose_bot_seats


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


def _auto_match_name(boundary: datetime) -> str:
    """Pick a name from _AUTO_MATCH_NAMES keyed to the boundary's 30-min slot."""
    slot = boundary.hour * 2 + boundary.minute // 30
    return f"{_AUTO_MATCH_NAMES[slot % len(_AUTO_MATCH_NAMES)]} Match"


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
        # Guard against arenas that lost their bots (e.g. a schema migration that
        # wiped the players table leaves the match row in REGISTERING with 0 bots).
        # If the bot count is short, cancel the stale arena and fall through to
        # create a fresh one so the poller self-heals without manual intervention.
        bot_count = (
            await db.scalar(
                select(func.count())
                .select_from(Player)
                .join(Agent, Agent.id == Player.agent_id)
                .where(
                    Player.match_id == existing.id,
                    Player.left_at.is_(None),
                    Agent.kind == AgentKind.BOT,
                )
            )
        ) or 0
        if bot_count >= PRACTICE_ARENA_SIM_COUNT:
            return
        existing.state = GameState.CANCELLED
        existing.cancelled_at = datetime.now(timezone.utc)
        await db.commit()

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
        total_rounds=PRACTICE_ARENA_TOTAL_ROUNDS,
        turns_per_round=PRACTICE_ARENA_TURNS_PER_ROUND,
        match_kind=MatchKind.PRACTICE_ARENA.value,
    )
    db.add(arena)
    await db.flush()

    seats = _choose_bot_seats(PRACTICE_ARENA_SIM_COUNT)
    try:
        await add_bots_to_game(db, arena, seats)
    except SimSeatingError as exc:
        logger.exception("Failed to seat bots in Practice Arena: %s", exc)
        await db.rollback()
        return

    logger.info("Created Practice Arena %s with %d bot seats.", match_id, len(seats))


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
    name = _auto_match_name(boundary)
    auto = Match(
        id=match_id,
        name=name,
        state=GameState.SCHEDULED,
        scheduled_start=boundary,
        min_players=1,
        max_players=AUTO_MATCH_MAX_PLAYERS,
        total_rounds=AUTO_MATCH_TOTAL_ROUNDS,
        turns_per_round=AUTO_MATCH_TURNS_PER_ROUND,
        match_kind=MatchKind.AUTO_SCHEDULED.value,
    )
    db.add(auto)
    await db.commit()
    logger.info("Created auto-match %s scheduled at %s.", match_id, boundary.isoformat())


async def fill_and_start_auto_matches(db: AsyncSession) -> None:
    """Fill and start due auto-matches only after an external agent joins."""
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
        active_players = (
            await db.execute(
                select(Player.seat_name, Agent.kind)
                .join(Agent, Agent.id == Player.agent_id)
                .where(Player.match_id == match.id, Player.left_at.is_(None))
            )
        ).all()
        has_external_agent = any(
            kind not in (AgentKind.BOT, AgentKind.BOT.value) for _, kind in active_players
        )
        if not has_external_agent:
            match.state = GameState.CANCELLED
            match.cancelled_at = now
            await db.commit()
            logger.info(
                "Cancelled auto-match %s: no external agents joined.", match.id
            )
            continue

        player_count = len(active_players)
        empty_slots = match.max_players - player_count
        if empty_slots > 0:
            n_sims = min(empty_slots, AUTO_MATCH_SIM_COUNT_MAX)
            agent_ids = {seat_name for seat_name, _ in active_players}
            seats = _choose_bot_seats(n_sims, used_names=agent_ids)
            if seats:
                try:
                    await add_bots_to_game(db, match, seats)
                except SimSeatingError as exc:
                    logger.exception(
                        "Failed to seat bots in auto-match %s: %s", match.id, exc
                    )
                    continue

        await start_game(db, match)
        logger.info("Started auto-match %s.", match.id)
