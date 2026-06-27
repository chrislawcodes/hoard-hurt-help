"""Managed match lifecycle for Practice Arena and Auto-Scheduled matches.

Two match types are always available to operators without admin intervention:

* **Practice Arena** — one open match, pre-seeded with bots, that starts the
  instant any human joins. Immediately replaced when the match ends.
* **Auto-Match** — one open match per 15-minute clock boundary. At its
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

from app.engine.bot_presets import BOT_PRESETS, allocate_default_bot_names, bot_presets
from app.engine.match_creation import create_match
from app.engine.bots.seating import BotSeatingError, add_bots_to_game
from app.engine.user_match_start import is_bot_kind
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.ops_events import log_ops_event

logger = logging.getLogger(__name__)

PRACTICE_ARENA_NAME = "Practice Arena"
PRACTICE_ARENA_BOT_COUNT = len(BOT_PRESETS)  # one bot per default strategy
# One open seat above the pre-seeded bots, so the lobby reads "8 / 9" and it's
# clear that a single human joining fills the match and starts the game.
PRACTICE_ARENA_MAX_PLAYERS = PRACTICE_ARENA_BOT_COUNT + 1
PRACTICE_ARENA_TOTAL_ROUNDS = 7
PRACTICE_ARENA_TURNS_PER_ROUND = 7

AUTO_MATCH_INTERVAL_MINUTES = 15
AUTO_MATCH_MAX_PLAYERS = 8
AUTO_MATCH_BOT_COUNT_MAX = 7
AUTO_MATCH_TOTAL_ROUNDS = 7
AUTO_MATCH_TURNS_PER_ROUND = 7

# Rotating names for auto-matches — one name per 15-min boundary slot (96/day),
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
    presets = bot_presets()
    if not presets:
        return []
    names = allocate_default_bot_names(n, used_names=used_names)
    seats = []
    for i in range(n):
        preset = presets[i % len(presets)]
        seats.append((names[i], preset.id))
    return seats


async def fill_match_with_bots(
    db: AsyncSession, match: Match, target_active: int
) -> int:
    """Seat bots until the match has ``target_active`` confirmed players.

    "Confirmed" = ``left_at IS NULL AND seat_reserved_until IS NULL`` — a held
    seat isn't a real player yet, and bots are always confirmed. Never seats past
    ``max_players`` (counting every not-left seat). Commits via
    :func:`add_bots_to_game`. Returns the number of bots seated — 0 if the match
    already meets the target or the table is full.
    """
    confirmed = (
        await db.scalar(
            select(func.count())
            .select_from(Player)
            .where(
                Player.match_id == match.id,
                Player.left_at.is_(None),
                Player.seat_reserved_until.is_(None),
            )
        )
    ) or 0
    seated = (
        await db.scalar(
            select(func.count())
            .select_from(Player)
            .where(Player.match_id == match.id, Player.left_at.is_(None))
        )
    ) or 0
    n_bots = min(max(0, target_active - confirmed), max(0, match.max_players - seated))
    if n_bots <= 0:
        return 0
    used_names = set(
        (
            await db.execute(
                select(Player.seat_name).where(
                    Player.match_id == match.id, Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    seats = _choose_bot_seats(n_bots, used_names=used_names)
    if not seats:
        return 0
    await add_bots_to_game(db, match, seats)
    return len(seats)


def _auto_match_name(boundary: datetime) -> str:
    """Pick a name from _AUTO_MATCH_NAMES keyed to the boundary's interval slot."""
    slots_per_hour = 60 // AUTO_MATCH_INTERVAL_MINUTES
    slot = boundary.hour * slots_per_hour + boundary.minute // AUTO_MATCH_INTERVAL_MINUTES
    return f"{_AUTO_MATCH_NAMES[slot % len(_AUTO_MATCH_NAMES)]} Match"


def _next_boundary() -> datetime:
    """Return the next AUTO_MATCH_INTERVAL_MINUTES clock boundary (UTC) from now."""
    now = datetime.now(timezone.utc)
    # Snap down to the current interval boundary, then step one interval forward.
    current_slot_minute = (now.minute // AUTO_MATCH_INTERVAL_MINUTES) * AUTO_MATCH_INTERVAL_MINUTES
    floor = now.replace(minute=current_slot_minute, second=0, microsecond=0)
    return floor + timedelta(minutes=AUTO_MATCH_INTERVAL_MINUTES)


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
        # Guard against stale arenas (e.g. one created before PRACTICE_ARENA_MAX_PLAYERS
        # changed still carries the old capacity in its row, or a schema migration that
        # wiped the players table left the row in REGISTERING with 0 bots). If the bot
        # count is short or the capacity is out of date, cancel the stale arena and fall
        # through to create a fresh one so the poller self-heals without manual
        # intervention. Only REGISTERING/SCHEDULED arenas reach here — once a human joins
        # the arena goes ACTIVE, so this never cancels a match a person is in.
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
        if (
            bot_count >= PRACTICE_ARENA_BOT_COUNT
            and existing.max_players == PRACTICE_ARENA_MAX_PLAYERS
        ):
            return
        existing.state = GameState.CANCELLED
        existing.cancelled_at = datetime.now(timezone.utc)
        await db.commit()

    presets = bot_presets()
    if not presets:
        logger.warning("No bot presets available — Practice Arena not created.")
        return

    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    arena = await create_match(
        db,
        game="hoard-hurt-help",
        name=PRACTICE_ARENA_NAME,
        scheduled_start=far_future,
        min_players=1,
        max_players=PRACTICE_ARENA_MAX_PLAYERS,
        per_turn_deadline_seconds=60,
        total_rounds=PRACTICE_ARENA_TOTAL_ROUNDS,
        turns_per_round=PRACTICE_ARENA_TURNS_PER_ROUND,
        state=GameState.REGISTERING,
        match_kind=MatchKind.PRACTICE_ARENA.value,
        commit=False,
    )

    seats = _choose_bot_seats(PRACTICE_ARENA_BOT_COUNT)
    try:
        await add_bots_to_game(db, arena, seats)
    except BotSeatingError as exc:
        log_ops_event(
            logger,
            logging.ERROR,
            "practice_arena_seating_failed",
            f"Failed to seat bots in Practice Arena: {exc}",
            match_id=arena.id,
        )
        await db.rollback()
        return

    logger.info("Created Practice Arena %s with %d bot seats.", arena.id, len(seats))


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
    name = _auto_match_name(boundary)
    auto = await create_match(
        db,
        game="hoard-hurt-help",
        name=name,
        scheduled_start=boundary,
        min_players=1,
        max_players=AUTO_MATCH_MAX_PLAYERS,
        per_turn_deadline_seconds=60,
        total_rounds=AUTO_MATCH_TOTAL_ROUNDS,
        turns_per_round=AUTO_MATCH_TURNS_PER_ROUND,
        state=GameState.SCHEDULED,
        match_kind=MatchKind.AUTO_SCHEDULED.value,
        commit=False,
    )
    await db.commit()
    logger.info("Created auto-match %s scheduled at %s.", auto.id, boundary.isoformat())


async def fill_and_start_auto_matches(db: AsyncSession) -> None:
    """Fill and start due auto-matches only after an external agent joins."""
    # Late import to avoid circular dependency: scheduler imports arena.
    from app.engine.scheduler import start_game

    now = datetime.now(timezone.utc)
    due_ids: list[str] = list(
        (
            await db.execute(
                select(Match.id).where(
                    Match.match_kind == MatchKind.AUTO_SCHEDULED.value,
                    Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                    Match.scheduled_start <= now,
                )
            )
        ).scalars().all()
    )

    for match_id in due_ids:
        # Re-fetch each match by ID so rollbacks on previous iterations do not
        # leave stale, expired ORM objects in the current session.
        match = await db.get(Match, match_id)
        if match is None:
            continue  # already removed by a concurrent process
        active_players = (
            await db.execute(
                select(Player.seat_name, Agent.kind)
                .join(Agent, Agent.id == Player.agent_id)
                .where(Player.match_id == match_id, Player.left_at.is_(None))
            )
        ).all()
        has_external_agent = any(
            not is_bot_kind(kind) for _, kind in active_players
        )
        if not has_external_agent:
            match.state = GameState.CANCELLED
            match.cancelled_at = now
            await db.commit()
            logger.info(
                "Cancelled auto-match %s: no external agents joined.", match_id
            )
            continue

        player_count = len(active_players)
        empty_slots = match.max_players - player_count
        if empty_slots > 0:
            n_bots = min(empty_slots, AUTO_MATCH_BOT_COUNT_MAX)
            agent_ids = {seat_name for seat_name, _ in active_players}
            seats = _choose_bot_seats(n_bots, used_names=agent_ids)
            if seats:
                try:
                    await add_bots_to_game(db, match, seats)
                except BotSeatingError as exc:
                    log_ops_event(
                        logger,
                        logging.ERROR,
                        "match_cancelled",
                        f"Auto-match {match_id} bot seating failed — cancelling match: {exc}",
                        match_id=match_id,
                        reason="seating_failure",
                    )
                    await db.rollback()
                    # Rollback expires all session objects; reload the match row
                    # before mutating it so we don't touch a stale in-memory state.
                    fresh = await db.get(Match, match_id)
                    if fresh is not None:
                        fresh.state = GameState.CANCELLED
                        fresh.cancelled_at = now
                        await db.commit()
                    continue

        await start_game(db, match)
        logger.info("Started auto-match %s.", match_id)
