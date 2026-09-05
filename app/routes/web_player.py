"""Guide, runner download, join, and player dashboard web routes.

This module is now a thin aggregator. The player-facing web surface that used to
live in this one file is split by responsibility into focused sibling modules:

  - ``web_guide``        — guide pages, runner/setup file downloads, legacy join
    redirects.
  - ``web_join``         — the AI picker, the join screen, and the join submit.
  - ``web_seat_connect`` — the held-seat connect countdown page and its poll.
  - ``web_my_matches``   — the 'my games' dashboard, the player slot dashboard,
    and the leave action.
  - ``web_player_shared``— small helpers used across more than one of the above
    (``_hx_redirect``, ``_seat_name``, ``_load_user_agents``,
    ``_seat_provider_readiness``, ``_seat_provider_label``).

``router`` here includes the sub-routers in the SAME order the routes were
registered before the split, so FastAPI route matching is identical.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import web_guide, web_join, web_my_matches, web_seat_connect

router = APIRouter(tags=["web"])
# Include the sub-routers in the original single-file registration order so route
# matching is identical: guide + file downloads + legacy join redirects, then the
# join flow, then the held-seat connect screens, then the player dashboards.
router.include_router(web_guide.router)
router.include_router(web_join.router)
router.include_router(web_seat_connect.router)
router.include_router(web_my_matches.router)

__all__ = ["router"]
