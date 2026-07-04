"""Shared admin/game-admin route logic.

The platform-admin JSON API (``/api/admin``, global) and the per-game admin
JSON API (``/api/game-admin/{game}``, scoped to one game) are near-identical:
they build the same ``GameRecord``, run the same create/cancel orchestration,
and serve the same CSV/JSON exports. The only real differences are the auth
dependency (owned by each route), the game-resolution source, and a couple of
error-shape choices on the create path.

This module owns the shared bodies, parameterized over those differences. The
route handlers stay thin wrappers that supply their own auth dependency and
scope, then delegate here. The export *serialization* lives in
``app.read_models.match_export``; this module only orchestrates load + scope.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.match_creation import create_match_with_state, player_count_error
from app.engine.match_deletion import cancel_blocked_reason, cancel_match
from app.games import GameError, get as get_game_module
from app.models.match import GameState, Match
from app.read_models.match_export import build_csv_export, build_json_export
from app.routes.web_match_loaders import load_match_or_404
from app.schemas.admin import CreateGameRequest, GameRecord

__all__ = [
    "build_game_record",
    "cancel_loaded_match",
    "create_game_record",
    "export_match_csv",
    "export_match_json",
    "load_match_or_404",
]


def build_game_record(match: Match) -> GameRecord:
    """Build the ``GameRecord`` response from a created/loaded match."""
    return GameRecord(
        id=match.id,
        name=match.name,
        state=match.state.value,
        scheduled_start=match.scheduled_start,
        started_at=match.started_at,
        completed_at=match.completed_at,
        cancelled_at=match.cancelled_at,
        min_players=match.min_players,
        max_players=match.max_players,
        per_turn_deadline_seconds=match.per_turn_deadline_seconds,
        current_round=match.current_round,
        current_turn=match.current_turn,
        rules_version=match.rules_version,
    )


async def create_game_record(
    db: AsyncSession,
    *,
    game: str,
    body: CreateGameRequest,
    created_by_user_id: int,
    game_not_found_status: int,
) -> GameRecord:
    """Validate a create request and persist the match, returning its record.

    Shared by both admin create handlers. The two clones differ only in how they
    react to an unresolvable game module: the platform-admin API raises 400 with
    the underlying ``GameError`` message, while the game-admin API raises 404
    with ``"Game not found."`` ``game_not_found_status`` selects which behavior
    the caller wants; the caller is responsible for any pre-check it needs (the
    game-admin route's ``known_types`` 400 guard stays in the route).
    """
    if body.scheduled_start <= datetime.now(timezone.utc):
        raise HTTPException(400, detail="scheduled_start must be in the future.")
    try:
        module = get_game_module(game)
    except GameError as exc:
        if game_not_found_status == 404:
            raise HTTPException(404, detail="Game not found.") from exc
        raise HTTPException(400, detail=str(exc)) from exc
    cfg = module.config_defaults()
    count_error = player_count_error(
        min_players=body.min_players,
        max_players=body.max_players,
        cfg_min_players=cfg.min_players,
        cfg_max_players=cfg.max_players,
        range_message=f"{game} supports {cfg.min_players}-{cfg.max_players} players.",
        order_message="min_players must be <= max_players.",
    )
    if count_error is not None:
        raise HTTPException(400, detail=count_error)
    match = await create_match_with_state(
        db,
        game=game,
        name=body.name,
        scheduled_start=body.scheduled_start,
        min_players=body.min_players,
        max_players=body.max_players,
        per_turn_deadline_seconds=body.per_turn_deadline_seconds,
        total_rounds=body.total_rounds,
        turns_per_round=body.turns_per_round,
        state=GameState.REGISTERING,
        created_by_user_id=created_by_user_id,
        state_config={
            "wild_ones": body.wild_ones,
            "dice_per_player": body.dice_per_player,
        },
    )
    return build_game_record(match)


async def cancel_loaded_match(db: AsyncSession, match: Match) -> None:
    """Cancel a loaded match, raising 409 when its state forbids cancel."""
    reason = cancel_blocked_reason(match)
    if reason is not None:
        raise HTTPException(409, detail=reason)
    await cancel_match(db, match)


async def export_match_csv(db: AsyncSession, match_id: str) -> StreamingResponse:
    """Build the CSV export response for a match id."""
    return await build_csv_export(db, match_id)


async def export_match_json(db: AsyncSession, match: Match) -> StreamingResponse:
    """Build the JSON export response for a loaded match."""
    return await build_json_export(db, match)
