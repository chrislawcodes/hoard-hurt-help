"""Admin JSON API: create/cancel games, export data."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from fastapi.responses import StreamingResponse

from app.deps import DbSession, require_platform_admin
from app.models.user import User
from app.routes.game_admin_actions import (
    cancel_loaded_match,
    create_game_record,
    export_match_csv,
    export_match_json,
)
from app.routes.web_match_loaders import load_match_or_404
from app.schemas.admin import CancelResponse, CreateGameRequest, GameRecord

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/matches", response_model=GameRecord, status_code=status.HTTP_201_CREATED)
@router.post("/games", response_model=GameRecord, status_code=status.HTTP_201_CREATED)
async def create_game(
    body: CreateGameRequest,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
) -> GameRecord:
    return await create_game_record(
        db,
        game=body.game_type,
        body=body,
        created_by_user_id=user.id,
        game_not_found_status=400,
    )


@router.post("/matches/{match_id}/cancel", response_model=CancelResponse)
@router.post("/games/{match_id}/cancel", response_model=CancelResponse)
async def cancel_game(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
) -> CancelResponse:
    g = await load_match_or_404(db, match_id)
    await cancel_loaded_match(db, g)
    return CancelResponse()


@router.get("/matches/{match_id}/export.csv")
@router.get("/games/{match_id}/export.csv")
async def export_csv(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
) -> StreamingResponse:
    await load_match_or_404(db, match_id)
    return await export_match_csv(db, match_id)


@router.get("/matches/{match_id}/export.json")
@router.get("/games/{match_id}/export.json")
async def export_json(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
) -> StreamingResponse:
    g = await load_match_or_404(db, match_id)
    return await export_match_json(db, g)
