"""Shared match creation helper for human-facing and automated creators."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.tokens import generate_match_id
from app.games import known_types
from app.models.game_state import MatchState
from app.models.match import GameState, Match, MatchKind


def player_count_error(
    *,
    min_players: int,
    max_players: int,
    cfg_min_players: int,
    cfg_max_players: int,
    range_message: str,
    order_message: str,
) -> str | None:
    """Validate requested player counts against a game's allowed range.

    HTTP-agnostic: returns an error string to surface, or ``None`` when the
    counts are valid. Each caller supplies its own ``range_message`` (used for
    both an out-of-range min and an out-of-range max, matching current
    behavior) and ``order_message`` (used when ``min_players > max_players``)
    so per-route wording stays exactly as it is today.
    """
    if not (cfg_min_players <= min_players <= cfg_max_players):
        return range_message
    if not (cfg_min_players <= max_players <= cfg_max_players):
        return range_message
    if min_players > max_players:
        return order_message
    return None


def _numeric_suffix(match_id: str) -> int | None:
    parts = match_id.split("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return None


async def allocate_match_id(db: AsyncSession) -> str:
    """Return the next available M_#### id from the current match rows."""
    existing_ids = (await db.execute(select(Match.id))).scalars().all()
    n = 0
    for match_id in existing_ids:
        if not match_id.startswith("M_"):
            continue
        suffix = _numeric_suffix(match_id)
        if suffix is not None and suffix > n:
            n = suffix
    return generate_match_id(n + 1)


async def create_match(
    db: AsyncSession,
    *,
    game: str,
    name: str,
    scheduled_start: datetime,
    min_players: int,
    max_players: int,
    per_turn_deadline_seconds: int,
    total_rounds: int,
    turns_per_round: int,
    state: GameState = GameState.REGISTERING,
    created_by_user_id: int | None = None,
    match_kind: str = MatchKind.MANUAL.value,
    commit: bool = True,
    max_attempts: int = 3,
) -> Match:
    """Create a match and retry on primary-key collision."""
    if game not in known_types():
        raise ValueError(f"Unknown game type {game!r}.")
    if scheduled_start.tzinfo is None:
        scheduled_start = scheduled_start.replace(tzinfo=timezone.utc)
    if scheduled_start <= datetime.now(timezone.utc):
        raise ValueError("scheduled_start must be in the future.")
    if not (1 <= min_players <= 20) or not (1 <= max_players <= 20):
        raise ValueError("Player counts must be 1 to 20.")
    if min_players > max_players:
        raise ValueError("Min players cannot be greater than max players.")
    if not (1 <= total_rounds <= 20):
        raise ValueError("Total rounds must be 1 to 20.")
    if not (1 <= turns_per_round <= 20):
        raise ValueError("Turns per round must be 1 to 20.")

    for attempt in range(max_attempts):
        match_id = await allocate_match_id(db)
        match = Match(
            id=match_id,
            game=game,
            name=name,
            state=state,
            scheduled_start=scheduled_start,
            min_players=min_players,
            max_players=max_players,
            per_turn_deadline_seconds=per_turn_deadline_seconds,
            total_rounds=total_rounds,
            turns_per_round=turns_per_round,
            created_by_user_id=created_by_user_id,
            match_kind=match_kind,
        )
        db.add(match)
        try:
            await db.flush()
            if commit:
                await db.commit()
            return match
        except IntegrityError:
            await db.rollback()
            if attempt + 1 == max_attempts:
                raise

    raise RuntimeError("match creation retry loop exhausted")


async def create_match_with_state(
    db: AsyncSession,
    *,
    game: str,
    name: str,
    scheduled_start: datetime,
    min_players: int,
    max_players: int,
    per_turn_deadline_seconds: int,
    total_rounds: int,
    turns_per_round: int,
    state_config: dict,
    state: GameState = GameState.REGISTERING,
    created_by_user_id: int | None = None,
    match_kind: str = MatchKind.MANUAL.value,
) -> Match:
    """Create a match and seed its module-owned ``MatchState`` in one commit.

    Wraps the shared ``create_match(..., commit=False)`` + ``MatchState`` insert
    + single ``commit`` that the admin creators all repeated. The caller builds
    ``state_config`` (stored as ``{"config": state_config}``); this helper owns
    only the persistence, not the config shape.
    """
    match = await create_match(
        db,
        game=game,
        name=name,
        scheduled_start=scheduled_start,
        min_players=min_players,
        max_players=max_players,
        per_turn_deadline_seconds=per_turn_deadline_seconds,
        total_rounds=total_rounds,
        turns_per_round=turns_per_round,
        state=state,
        created_by_user_id=created_by_user_id,
        match_kind=match_kind,
        commit=False,
    )
    db.add(MatchState(match_id=match.id, state_json={"config": state_config}))
    await db.commit()
    return match
