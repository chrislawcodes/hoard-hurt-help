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
URL surface is unchanged. Public symbols other modules and tests import are
re-exported below (see ``__all__``) so their import paths keep resolving.
"""

from __future__ import annotations

from app.routes import connections_machine_setup, connections_pages
from app.routes.connections_connect_guide import (
    ConnectOption,
    _connect_options,
    _play_prompt,
    _provider_label,
    _setup_message,
)
from app.routes.connections_machine_setup import (
    _ensure_pending_setup_and_key,
    _issue_setup_key,
    _load_owned_connection_setup,
    _load_resumeable_pending_setup,
    _validate_nickname_length,
)
from app.routes.connections_pages import (
    connection_detail,
    connection_health_badge_fragment,
    connection_status_fragment,
    list_connections,
    live_status_fragment,
)
from app.routes.connections_queries import (
    AgentRow,
    _connection_display_name,
    _live_status_context,
    _load_attached_agents,
    _load_connection_providers,
    _load_owned_connection,
    _load_stranded_agents,
    _load_user_agents,
    _summarize_agent,
)

# Re-exports — these names keep resolving from ``app.routes.connections_setup`` so
# existing imports (other route modules and the test suite) do not break.
__all__ = [
    "router",
    "AgentRow",
    "ConnectOption",
    "_connect_options",
    "_connection_display_name",
    "_ensure_pending_setup_and_key",
    "_issue_setup_key",
    "_live_status_context",
    "_load_attached_agents",
    "_load_connection_providers",
    "_load_owned_connection",
    "_load_owned_connection_setup",
    "_load_resumeable_pending_setup",
    "_load_stranded_agents",
    "_load_user_agents",
    "_play_prompt",
    "_provider_label",
    "_setup_message",
    "_summarize_agent",
    "_validate_nickname_length",
    "connection_detail",
    "connection_health_badge_fragment",
    "connection_status_fragment",
    "list_connections",
    "live_status_fragment",
]

# The aggregated router IS the pages router (it carries the empty-path
# ``list_connections`` route, which FastAPI rejects when re-included into an
# empty-prefix parent). We then fold the machine-setup actions onto it so a single
# router carries the full URL surface when ``app.main`` mounts it under
# ``/me/connections`` — identical to the pre-split registration.
router = connections_pages.router
router.include_router(connections_machine_setup.router)
