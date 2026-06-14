"""User-facing match creation and ownership routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.config import settings
from app.deps import DbSession, require_platform_admin, require_user
from app.engine.match_creation import create_match
from app.engine.match_deletion import cancel_match, delete_match
from app.games import GameError, get as get_game_module, is_admin_only
from app.models.match import GameState, Match
from app.models.user import User, UserRole
from app.routes.web_support import _is_any_admin, _load_match_or_404
from app.templating import templates

router = APIRouter(tags=["web"])

_CREATE_DEFAULTS = {
    "min_players": 6,
    "max_players": 20,
    "per_turn_deadline_seconds": 60,
    "total_rounds": 7,
    "turns_per_round": 7,
}


def _load_game_module_or_404(game: str):
    try:
        return get_game_module(game)
    except GameError as exc:
        raise HTTPException(status_code=404, detail="Game not found.") from exc


def _load_visible_game_module_or_404(game: str, user: User | None):
    """Like `_load_game_module_or_404`, but an admin-only (under-construction)
    game is invisible (404) to non-admins so they can't create matches for it."""
    module = _load_game_module_or_404(game)
    if is_admin_only(game) and not _is_any_admin(user):
        raise HTTPException(status_code=404, detail="Game not found.")
    return module


def _html_error(
    request: Request,
    user: User,
    game: str,
    *,
    message: str,
    status_code: int = 400,
):
    module = _load_game_module_or_404(game)
    return templates.TemplateResponse(
        request,
        "matches_user/create_match.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game_slug": game,
            "game_theme": module.theme(),
            "defaults": _CREATE_DEFAULTS,
            "error": message,
        },
        status_code=status_code,
    )


@router.get("/games/{game}/matches/new", response_class=HTMLResponse)
async def create_match_form(
    game: Annotated[str, Path()],
    request: Request,
    user: Annotated[User, Depends(require_user)],
):
    module = _load_visible_game_module_or_404(game, user)
    return templates.TemplateResponse(
        request,
        "matches_user/create_match.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game_slug": game,
            "game_theme": module.theme(),
            "defaults": _CREATE_DEFAULTS,
            "error": None,
        },
    )


@router.post("/games/{game}/matches/new")
async def create_match_submit(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
    scheduled_start: Annotated[str, Form()],
):
    _load_visible_game_module_or_404(game, user)  # 404 on unknown/hidden game
    try:
        when = datetime.fromisoformat(scheduled_start.replace("Z", "+00:00"))
    except ValueError:
        return _html_error(
            request,
            user,
            game,
            message="Could not read the start time. Please pick a date and time.",
        )
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when <= datetime.now(timezone.utc):
        return _html_error(
            request,
            user,
            game,
            message="Start time must be in the future.",
        )
    if user.role != UserRole.ADMIN:
        active_count = (
            await db.scalar(
                select(func.count())
                .select_from(Match)
                .where(
                    Match.created_by_user_id == user.id,
                    Match.state.in_(
                        [GameState.SCHEDULED, GameState.REGISTERING, GameState.ACTIVE]
                    ),
                )
            )
        ) or 0
        if active_count >= settings.user_active_match_limit:
            return _html_error(
                request,
                user,
                game,
                message=(
                    f"You can have at most {settings.user_active_match_limit} "
                    "active matches at once."
                ),
                status_code=status.HTTP_409_CONFLICT,
            )

    try:
        await create_match(
            db,
            game=game,
            name=name,
            scheduled_start=when,
            min_players=_CREATE_DEFAULTS["min_players"],
            max_players=_CREATE_DEFAULTS["max_players"],
            per_turn_deadline_seconds=_CREATE_DEFAULTS["per_turn_deadline_seconds"],
            total_rounds=_CREATE_DEFAULTS["total_rounds"],
            turns_per_round=_CREATE_DEFAULTS["turns_per_round"],
            state=GameState.REGISTERING,
            created_by_user_id=user.id,
        )
    except ValueError as exc:
        return _html_error(request, user, game, message=str(exc))

    return RedirectResponse(url="/me/matches", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/matches/{match_id}/delete")
async def delete_match_submit(
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    match = await _load_match_or_404(db, match_id)
    if user.role != UserRole.ADMIN and match.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "NOT_MATCH_OWNER",
                    "message": "You can only delete matches you created.",
                    "details": {},
                }
            },
        )
    if user.role != UserRole.ADMIN and match.state not in (
        GameState.SCHEDULED,
        GameState.REGISTERING,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "MATCH_ALREADY_STARTED",
                    "message": "Match already started.",
                    "details": {},
                }
            },
        )
    await delete_match(db, match.id)
    return RedirectResponse(url="/me/matches", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/matches/{match_id}/cancel")
async def cancel_match_submit(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
):
    # Cancel is an admin-only power (admins are the "organizers"). Regular users
    # cannot cancel — they can only delete their own match before it starts.
    # Cancel preserves data, so it is allowed from any non-terminal state
    # (including ACTIVE); only already-ended matches are rejected.
    match = await _load_match_or_404(db, match_id)
    if match.state in (GameState.COMPLETED, GameState.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "MATCH_ALREADY_ENDED",
                    "message": "Match already ended.",
                    "details": {},
                }
            },
        )
    await cancel_match(db, match)
    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_303_SEE_OTHER)
