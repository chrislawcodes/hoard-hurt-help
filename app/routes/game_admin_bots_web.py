"""Game-admin HTML pages — bot seating for a match."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.deps import DbSession, require_game_admin
from app.engine.bot_presets import bot_preset_by_id
from app.engine.bots import validate_bot_profile_fields
from app.engine.bots.roster import PACKS, PERSONALITIES, BOT_NAME_POOL
from app.engine.bots.seating import BotSeatingError, add_bots_to_game
from app.models.agent import AgentKind
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User
from app.routes.game_admin_web import _load_game_match_or_404
from app.templating import templates

router = APIRouter(prefix="/games/{game}/admin", tags=["game-admin"])


async def _render_add_bots(
    request: Request,
    db,
    user: User,
    game: str,
    match: Match,
    *,
    error: str | None = None,
    prefill: list[tuple[str, str]] | None = None,
    status_code: int = 200,
):
    existing = list(
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
    can_add = match.state in (GameState.SCHEDULED, GameState.REGISTERING)
    bots_data = {
        "maxPlayers": match.max_players,
        "currentCount": len(existing),
        "existing": existing,
        "names": list(BOT_NAME_POOL),
        "personalities": [
            {"id": p.id, "label": p.label, "description": p.description, "lean": p.lean}
            for p in PERSONALITIES
        ],
        "packs": [
            {
                "id": pk.id,
                "label": pk.label,
                "description": pk.description,
                "strategies": list(pk.strategies),
            }
            for pk in PACKS
        ],
        "prefill": [{"name": n, "strategy": s} for n, s in (prefill or [])],
    }
    return templates.TemplateResponse(
        request,
        "game_admin/add_bots.html",
        {
            "user": user,
            "is_admin": True,
            "game_slug": game,
            "match": match,
            "personalities": PERSONALITIES,
            "packs": PACKS,
            "can_add": can_add,
            "current_count": len(existing),
            "error": error,
            "bots_data": bots_data,
        },
        status_code=status_code,
    )


@router.get("/matches/{match_id}/bots", response_class=HTMLResponse)
async def add_bots_form(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
):
    g = await _load_game_match_or_404(db, game, match_id)
    return await _render_add_bots(request, db, user, game, g)


@router.post("/matches/{match_id}/bots")
async def add_bots_submit(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
    seat_name: Annotated[list[str] | None, Form()] = None,
    seat_strategy: Annotated[list[str] | None, Form()] = None,
):
    g = await _load_game_match_or_404(db, game, match_id)
    if g.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        return await _render_add_bots(
            request,
            db,
            user,
            game,
            g,
            error="Bots can only be added before a match starts.",
            status_code=409,
        )
    names = [n.strip() for n in (seat_name or [])]
    strategies = [s.strip() for s in (seat_strategy or [])]
    if len(names) != len(strategies):
        return await _render_add_bots(
            request,
            db,
            user,
            game,
            g,
            error="Something went wrong reading the roster. Please try again.",
            status_code=400,
        )
    seats = list(zip(names, strategies))
    # Validate each bot configuration before seating so the user gets a clear
    # form error instead of a late failure inside add_bots_to_game.  We only
    # validate bot-kind entries (this form exclusively creates bots, so the
    # guard is always true, but the check is explicit for clarity).
    for seat_name_val, seat_strategy_val in seats:
        preset = bot_preset_by_id(seat_strategy_val)
        if preset is None:
            # Unknown personality ID — build a legible error before seating.
            return await _render_add_bots(
                request,
                db,
                user,
                game,
                g,
                error=f"Unknown bot strategy {seat_strategy_val!r} for bot {seat_name_val!r}.",
                prefill=seats,
                status_code=400,
            )
        try:
            validate_bot_profile_fields(
                kind=AgentKind.BOT,
                bot_strategy=preset.strategy,
                bot_truthfulness=preset.truthfulness,
                bot_trust_model=preset.trust_model,
                bot_seed=preset.seed_offset,  # non-None int; seating overwrites with agent.id
                bot_version="v1",
            )
        except ValueError as exc:
            return await _render_add_bots(
                request,
                db,
                user,
                game,
                g,
                error=f"Invalid bot configuration for {seat_name_val!r}: {exc}",
                prefill=seats,
                status_code=400,
            )
    try:
        created = await add_bots_to_game(db, g, seats)
    except BotSeatingError as exc:
        return await _render_add_bots(
            request, db, user, game, g, error=str(exc), prefill=seats, status_code=400
        )
    return RedirectResponse(
        url=f"/games/{game}/admin/matches/{match_id}?added={len(created)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
