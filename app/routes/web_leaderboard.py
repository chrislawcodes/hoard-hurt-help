"""Global leaderboard web route, grouped by game."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, get_current_user
from app.games import is_admin_only
from app.read_models.leaderboard_cache import load_leaderboard_sections_cached
from app.routes.web_support import _is_any_admin
from app.templating import templates

router = APIRouter(tags=["web"])


def _leaderboard_url(
    request: Request,
    *,
    rating: str | None = None,
    included: str | None = None,
    hide_sim_games: bool | None = None,
) -> str:
    """Build a leaderboard link while preserving the other active filters."""

    params = dict(request.query_params)
    if rating is not None:
        params["rating"] = rating
    if included is not None:
        params["included"] = included
    if hide_sim_games is not None:
        if hide_sim_games:
            params["hide_sim_games"] = "1"
        else:
            params.pop("hide_sim_games", None)
    return f"/leaderboard?{urlencode(params)}" if params else "/leaderboard"


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(
    request: Request,
    db: DbSession,
    rating: str = "standard",
    included: str = "agents",
    hide_sim_games: bool = False,
):
    """Global leaderboard, grouped by game."""
    user = await get_current_user(request, db)
    rating_mode = "bonus" if rating == "bonus" else "standard"
    included_mode = "sims" if included == "sims" else "all" if included == "all" else "agents"
    sections = await load_leaderboard_sections_cached(
        rating_mode=rating_mode,
        included=included_mode,
    )
    if hide_sim_games:
        sections = [section for section in sections if not section.has_bots]
    # Hide admin-only (under-construction) game sections from non-admins.
    if not _is_any_admin(user):
        sections = [s for s in sections if not is_admin_only(s.game_type)]
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "sections": sections,
            "rating_mode": rating_mode,
            "included": included_mode,
            "hide_sim_games": hide_sim_games,
            "rating_standard_url": _leaderboard_url(
                request, rating="standard", included=included_mode, hide_sim_games=hide_sim_games
            ),
            "rating_bonus_url": _leaderboard_url(
                request, rating="bonus", included=included_mode, hide_sim_games=hide_sim_games
            ),
            "included_agents_url": _leaderboard_url(
                request, rating=rating_mode, included="agents", hide_sim_games=hide_sim_games
            ),
            "included_bots_url": _leaderboard_url(
                request, rating=rating_mode, included="sims", hide_sim_games=hide_sim_games
            ),
            "included_all_url": _leaderboard_url(
                request, rating=rating_mode, included="all", hide_sim_games=hide_sim_games
            ),
            "bot_games_show_url": _leaderboard_url(
                request, rating=rating_mode, included=included_mode, hide_sim_games=False
            ),
            "bot_games_hide_url": _leaderboard_url(
                request, rating=rating_mode, included=included_mode, hide_sim_games=True
            ),
        },
    )


__all__ = ["router", "leaderboard_page", "_leaderboard_url"]
