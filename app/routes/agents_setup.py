"""Agent list, creation, and detail routes.

The agent setup surface is split into focused route modules; this file keeps the
single aggregated router that ``app.main`` mounts at ``/me/agents`` and
re-exports the public symbols other modules and tests import from here.

Sub-routers are included list -> create -> detail so the literal ``/new`` path
is registered before the ``/{agent_id}`` capture route.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import agents_create, agents_detail, agents_list
from app.routes.agents_create import clean_agent_name
from app.routes.agents_detail import _build_agent_detail_context, _load_agent_matches
from app.routes.agents_health_presenter import _is_ready_to_play

router = APIRouter()
# Adopt each sub-router's routes directly rather than via include_router. The
# list page is served at the empty path (""), which include_router rejects when
# the parent prefix is also empty ("Prefix and path cannot be both empty").
# Splicing the routes preserves every path, method, and dependency exactly while
# keeping the empty-path route, so app.main can still mount this at /me/agents.
# Order is list -> create -> detail so the literal /new path is registered
# before the /{agent_id} capture route.
router.routes.extend(agents_list.router.routes)
router.routes.extend(agents_create.router.routes)
router.routes.extend(agents_detail.router.routes)

__all__ = [
    "router",
    "clean_agent_name",
    "_build_agent_detail_context",
    "_load_agent_matches",
    "_is_ready_to_play",
]
