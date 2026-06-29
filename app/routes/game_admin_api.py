"""Game-admin JSON API — per-game match management (create/cancel/export)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse

from app.deps import DbSession, require_game_admin
from app.games import known_types
from app.models.user import User
from app.routes.game_admin_actions import (
    cancel_loaded_match,
    create_game_record,
    export_match_csv,
    export_match_json,
)
from app.routes.web_support import load_game_match_or_404
from app.schemas.admin import CancelResponse, CreateGameRequest, GameRecord

router = APIRouter(prefix="/api/game-admin/{game}", tags=["game-admin"])


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
    return await create_game_record(
        db,
        game=game,
        body=body,
        created_by_user_id=user.id,
        game_not_found_status=404,
    )


@router.post("/matches/{match_id}/cancel", response_model=CancelResponse)
async def cancel_game(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_game_admin)],
) -> CancelResponse:
    g = await load_game_match_or_404(db, game, match_id)
    await cancel_loaded_match(db, g)
    return CancelResponse()


@router.get("/matches/{match_id}/export.csv")
async def export_csv(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_game_admin)],
) -> StreamingResponse:
    await load_game_match_or_404(db, game, match_id)
    return await export_match_csv(db, match_id)


@router.get("/matches/{match_id}/export.json")
async def export_json(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_game_admin)],
) -> StreamingResponse:
    g = await load_game_match_or_404(db, game, match_id)
    return await export_match_json(db, g)
