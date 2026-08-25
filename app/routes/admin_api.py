"""Admin JSON API: create/cancel games, export data."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import StreamingResponse

from app.deps import DbSession, require_platform_admin
from app.models.user import User
from app.read_models.match_export import ExportViewer
from app.routes.admin_match_actions import (
    cancel_loaded_match,
    create_game_record,
    export_match_csv,
    export_match_json,
)
from app.routes.web_match_loaders import load_match_or_404
from app.schemas.admin import CancelResponse, CreateGameRequest, GameRecord

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin_viewer(user: User) -> ExportViewer:
    """This whole router is platform-admin gated, so the export is unredacted."""
    return ExportViewer(user_id=user.id, is_platform_admin=True)


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
    )


@router.post("/matches/{match_id}/cancel", response_model=CancelResponse)
@router.post("/games/{match_id}/cancel", response_model=CancelResponse)
async def cancel_game(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
    allow_active: Annotated[
        bool,
        Query(
            description=(
                "Also cancel a match that is already running. Off by default so "
                "a live match cannot be stopped by an accidental call."
            )
        ),
    ] = False,
) -> CancelResponse:
    """Cancel a match. This JSON route is the ONLY way to stop a running one.

    Deliberately not surfaced in the web UI: stopping a live match is a rare
    operator action, and a button for it sits one misclick from destroying a
    measurement run that takes ~25 minutes to reproduce. Admins call it with
    ``?allow_active=true``.

    The agents already handle their own shutdown. ``run_all.sh`` watches the
    public match state and writes its STOP file as soon as the match reads
    cancelled, so the play loops wind down on their own rather than relaunching
    into a match that no longer exists.
    """
    g = await load_match_or_404(db, match_id)
    await cancel_loaded_match(db, g, allow_active=allow_active)
    return CancelResponse()


@router.get("/matches/{match_id}/export.csv")
@router.get("/games/{match_id}/export.csv")
async def export_csv(
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
) -> StreamingResponse:
    await load_match_or_404(db, match_id)
    return await export_match_csv(db, match_id, viewer=_admin_viewer(user))


@router.get("/matches/{match_id}/export.json")
@router.get("/games/{match_id}/export.json")
async def export_json(
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
) -> StreamingResponse:
    g = await load_match_or_404(db, match_id)
    return await export_match_json(db, g, viewer=_admin_viewer(user))
