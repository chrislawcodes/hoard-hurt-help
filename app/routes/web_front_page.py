"""Agent Ludum platform marketing front page (`/`)."""

from __future__ import annotations

import asyncio
import dataclasses

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, get_current_user
from app.games import is_admin_only
from app.read_models.leaderboard_cache import load_leaderboard_sections_cached
from app.routes.showcase_replay import load_showcase_replay_cached
from app.routes.web_support import _is_any_admin
from app.templating import templates

router = APIRouter(tags=["web"])


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: DbSession):
    """Agent Ludum platform front page (marketing).

    Static explainer + funnel, plus two real-data regions: the hero match card
    (a real finished game's replay) and the leaderboard band (real standings).
    Both come from stale-while-revalidate caches — the showcase game and the
    standings are the same for every visitor, so a stale request is served the
    cached copy instantly while a background refresh runs. Both fall back to
    honest empty states. The two cached reads are independent, so we run them
    concurrently (each manages its own session). The Hoard·Hurt·Help lobby
    itself lives one level down at `/games/hoard-hurt-help`.
    """
    user = await get_current_user(request, db)
    viewer_is_admin = _is_any_admin(user)

    # Two independent cached reads, run concurrently.
    (rc_game_id, rc_data, rc_game_type), lb_sections_full = await asyncio.gather(
        load_showcase_replay_cached(),
        load_leaderboard_sections_cached(included="all"),
    )

    # Leaderboard band: top 8 per game section for the home page teaser.
    lb_sections = [dataclasses.replace(s, rows=s.rows[:8]) for s in lb_sections_full]
    if not viewer_is_admin:
        lb_sections = [s for s in lb_sections if not is_admin_only(s.game_type)]

    return templates.TemplateResponse(
        request,
        "agent_ludum.html",
        {
            "user": user,
            "is_admin": viewer_is_admin,
            "rc_data": rc_data,
            "rc_game_id": rc_game_id,
            "rc_game_type": rc_game_type,
            "lb_sections": lb_sections,
        },
    )


__all__ = ["router", "home"]
