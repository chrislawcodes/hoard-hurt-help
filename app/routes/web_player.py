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
registered before the split, so FastAPI route matching is identical. The public
symbols other modules and tests import from this module are re-exported below
(see ``__all__``) so their import paths keep resolving — in particular
``from app.routes.web_player import _seat_name``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import web_guide, web_join, web_my_matches, web_seat_connect
from app.routes.web_guide import (
    _serve_agent_file,
    agent_runner_script,
    agent_setup_file,
    guide,
    legacy_join_form_redirect,
    legacy_join_submit_redirect,
)
from app.routes.web_join import (
    _build_ai_options,
    _default_human_choice,
    _seat_user_agent,
    join_form,
    join_submit,
)
from app.routes.web_my_matches import (
    my_games_redirect,
    my_matches,
    player_dashboard,
    web_leave,
)
from app.routes.web_player_shared import (
    _hx_redirect,
    _load_user_agents,
    _seat_name,
    _seat_provider_label,
    _seat_provider_readiness,
)
from app.routes.web_seat_connect import seat_connect, seat_connect_status

router = APIRouter(tags=["web"])
# Include the sub-routers in the original single-file registration order so route
# matching is identical: guide + file downloads + legacy join redirects, then the
# join flow, then the held-seat connect screens, then the player dashboards.
router.include_router(web_guide.router)
router.include_router(web_join.router)
router.include_router(web_seat_connect.router)
router.include_router(web_my_matches.router)

__all__ = [
    "router",
    # Shared helpers.
    "_hx_redirect",
    "_seat_name",
    "_load_user_agents",
    "_seat_provider_readiness",
    "_seat_provider_label",
    # Guide + downloads + legacy redirects.
    "guide",
    "_serve_agent_file",
    "agent_setup_file",
    "agent_runner_script",
    "legacy_join_form_redirect",
    "legacy_join_submit_redirect",
    # Join flow.
    "_build_ai_options",
    "_default_human_choice",
    "join_form",
    "_seat_user_agent",
    "join_submit",
    # Held-seat connect screens.
    "seat_connect",
    "seat_connect_status",
    # My games / dashboards / leave.
    "my_matches",
    "my_games_redirect",
    "player_dashboard",
    "web_leave",
]
