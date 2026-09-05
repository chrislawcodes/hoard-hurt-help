"""Connection list, creation, and detail routes.

This module is now a thin aggregator. The connections surface was split by
responsibility into focused modules:

  - ``connections_connect_guide`` — connect instructions, play-prompt, setup
    message, provider label/CLI tables.
  - ``connections_queries`` — shared read queries (agents, owned connection,
    provider toggles, live-status context).
  - ``connections_machine_setup`` — minting the pending setup + key, the name
    action, and the setup detail/status views.
  - ``connections_pages`` — the list/detail pages and their poll fragments.

``router`` here aggregates the page and machine-setup sub-routers so the mounted
URL surface is unchanged.
"""

from __future__ import annotations

from app.routes import connections_machine_setup, connections_pages

__all__ = ["router"]

# The aggregated router IS the pages router (it carries the empty-path
# ``list_connections`` route, which FastAPI rejects when re-included into an
# empty-prefix parent). We then fold the machine-setup actions onto it so a single
# router carries the full URL surface when ``app.main`` mounts it under
# ``/me/connections`` — identical to the pre-split registration.
router = connections_pages.router
router.include_router(connections_machine_setup.router)
