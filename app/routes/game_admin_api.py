"""Game-admin JSON API — per-game match management (create/cancel/export)."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.deps import DbSession, require_game_admin
from app.engine.match_creation import create_match_with_state, player_count_error
from app.engine.match_deletion import cancel_blocked_reason, cancel_match
from app.games import GameError, get as get_game_module, known_types
from app.models.match import Match, GameState
from app.models.user import User
from app.read_models.match_export import build_csv_export, build_json_export
from app.schemas.admin import CancelResponse, CreateGameRequest, GameRecord

router = APIRouter(prefix="/api/game-admin/{game}", tags=["game-admin"])


async def _load_game_match_or_404(db, game: str, match_id: str) -> Match:
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None or g.game != game:
        raise HTTPException(404)
    return g


@router.post("/matches", response_model=GameRecord, status_code=status.HTTP_201_CREATED)
async def create_game(
    game: Annotated[str, Path()],
    body: CreateGameRequest,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
) -> GameRecord:
    if game not in known_types():
        raise HTTPException(
            400, detail=f"Unknown game type {game!r}. Known: {known_types()}."
        )
    if body.scheduled_start <= datetime.now(timezone.utc):
        raise HTTPException(400, detail="scheduled_start must be in the future.")
    try:
        module = get_game_module(game)
    except GameError as exc:
        raise HTTPException(404, detail="Game not found.") from exc
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
    g = await create_match_with_state(
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
        created_by_user_id=user.id,
        state_config={
            "wild_ones": body.wild_ones,
            "dice_per_player": body.dice_per_player,
        },
    )
    return GameRecord(
        id=g.id,
        name=g.name,
        state=g.state.value,
        scheduled_start=g.scheduled_start,
        started_at=g.started_at,
        completed_at=g.completed_at,
        cancelled_at=g.cancelled_at,
        min_players=g.min_players,
        max_players=g.max_players,
        per_turn_deadline_seconds=g.per_turn_deadline_seconds,
        current_round=g.current_round,
        current_turn=g.current_turn,
        rules_version=g.rules_version,
    )


@router.post("/matches/{match_id}/cancel", response_model=CancelResponse)
async def cancel_game(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_game_admin)],
) -> CancelResponse:
    g = await _load_game_match_or_404(db, game, match_id)
    reason = cancel_blocked_reason(g)
    if reason is not None:
        raise HTTPException(409, detail=reason)
    await cancel_match(db, g)
    return CancelResponse()


@router.get("/matches/{match_id}/export.csv")
async def export_csv(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_game_admin)],
) -> StreamingResponse:
    await _load_game_match_or_404(db, game, match_id)
    return await build_csv_export(db, match_id)


@router.get("/matches/{match_id}/export.json")
async def export_json(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_game_admin)],
) -> StreamingResponse:
    g = await _load_game_match_or_404(db, game, match_id)
    return await build_json_export(db, g)
