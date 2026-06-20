"""Game catalog and play-hub web routes.

Covers the catalog of playable titles (`/games`), the `/play` hub that sends each
visitor to the lobby (signed in) or sign-in (signed out), and the per-game
agent-instructions page.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import DbSession, get_current_user
from app.games import get as get_game_module
from app.games import is_admin_only, visible_types
from app.games.base import GameError
from app.routes.nav_context import LOBBY_URL
from app.routes.web_support import _is_any_admin
from app.templating import templates

router = APIRouter(tags=["web"])


def _game_display_name(game_type: str) -> str:
    # The display title is owned by the game module. An unregistered (legacy)
    # game_type has no module, so fall back to the humanized type — exactly what
    # the module's own default does for a game that declares no title.
    try:
        return get_game_module(game_type).display_name()
    except GameError:
        return game_type.replace("-", " ").title()


def _game_tagline(game_type: str) -> str:
    # Owned by the game module; an unregistered type has no tagline.
    try:
        return get_game_module(game_type).tagline()
    except GameError:
        return ""


@router.get("/games", response_class=HTMLResponse)
async def games_catalog(request: Request, db: DbSession):
    """Catalog of the platform's playable game titles."""
    user = await get_current_user(request, db)
    is_admin = _is_any_admin(user)
    games = [
        {
            "slug": slug,
            "name": _game_display_name(slug),
            "tagline": _game_tagline(slug),
            "admin_only": is_admin_only(slug),
        }
        for slug in visible_types(include_admin_only=is_admin)
    ]
    return templates.TemplateResponse(
        request,
        "games.html",
        {
            "user": user,
            "is_admin": is_admin,
            "games": games,
        },
    )


@router.get("/play")
async def operator_join_page(request: Request, db: DbSession):
    """Dumb redirect into the game.

    Not signed in → sign in (returning to the game page).
    Signed in → the lobby. All setup gating (handle, agent, MCP connection,
    live) lives on the join flow, not here.
    """
    user = await get_current_user(request, db)

    if user is None:
        return RedirectResponse(
            "/auth/google/login?next=/games/hoard-hurt-help", status_code=status.HTTP_302_FOUND
        )

    return RedirectResponse(LOBBY_URL, status_code=status.HTTP_302_FOUND)


@router.get("/games/{game}/agent-instructions", response_class=HTMLResponse)
async def agent_instructions_page(
    request: Request,
    db: DbSession,
    game: Annotated[str, Path()],
):
    """Show the canonical base prompt supplied separately from agent strategy."""
    try:
        module = get_game_module(game)
    except GameError as exc:
        raise HTTPException(status_code=404, detail="Game not found.") from exc
    user = await get_current_user(request, db)
    defaults = module.config_defaults()
    base_prompt = module.agent_base_prompt(
        your_agent_id="<your agent ID>",
        all_agent_ids=["<your agent ID>", "<other agent IDs>"],
        total_rounds=defaults.total_rounds,
        turns_per_round=defaults.turns_per_round,
    )
    return templates.TemplateResponse(
        request,
        "agent_instructions.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game": game,
            "game_name": _game_display_name(game),
            "game_theme": module.theme(),
            "base_prompt": base_prompt,
        },
    )


__all__ = [
    "router",
    "games_catalog",
    "operator_join_page",
    "agent_instructions_page",
    "_game_display_name",
    "_game_tagline",
]
