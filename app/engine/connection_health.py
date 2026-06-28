"""Operational health for a Connection and the agents it powers.

This module is now a thin aggregator. What used to live in this one file is split
by responsibility into three focused sibling modules, layered acyclically so each
depends only on the ones below it:

  - ``connection_health_badge`` — the ``ConnectionHealth`` badge state machine and
    the liveness primitives every other layer reuses (``within_window``,
    ``_connection_is_live``, ``humanize_since``, ``agent_is_defaulting``,
    ``compute_connection_health``, the window constants). No deps on the others.
  - ``provider_readiness``      — provider-based coverage and the
    ``ProviderReadiness`` ladder (the shared ``_provider_connections_query``, the
    ``provider_*`` predicates, ``enabled_provider_values``,
    ``user_play_readiness``). Builds on the badge layer.
  - ``join_gate_capacity``      — active-match counts vs. live connection capacity
    (``active_matches_for_*``, ``live_*_capacity``, ``is_join_blocked``,
    ``providers_busy_for_user``). Builds on the readiness + badge layers.

The public (and previously module-private) symbols other modules and tests import
from ``app.engine.connection_health`` are re-exported below (see ``__all__``) so
every existing ``from app.engine.connection_health import X`` keeps resolving
unchanged.
"""

from __future__ import annotations

from app.engine.connection_health_badge import (
    _HEALTH_PRESENTATION,
    _HEARTBEAT_THROTTLE_SECONDS,
    LIVE_WINDOW_SECONDS,
    LOOP_RUNNING_WINDOW_SECONDS,
    CalmConnectionStatus,
    ConnectionHealth,
    ConnectionHealthStatus,
    _connection_is_live,
    within_window,
    agent_is_defaulting,
    calm_connection_status,
    compute_connection_health,
    humanize_since,
)
from app.engine.join_gate_capacity import (
    active_matches_for_provider,
    active_matches_for_user,
    is_join_blocked,
    live_provider_capacity,
    live_user_capacity,
    providers_busy_for_user,
)
from app.engine.provider_readiness import (
    MCP_CONNECTION_PROVIDERS,
    MCP_CONNECTION_VALID_DAYS,
    ProviderReadiness,
    _provider_connections_query,
    enabled_provider_values,
    provider_enabled_on_any_connection,
    provider_has_current_setup,
    provider_has_live_current_setup,
    provider_has_machine_connection,
    provider_has_recent_mcp_connection,
    provider_is_covered,
    provider_loop_running,
    provider_readiness,
    provider_uses_mcp_connection,
    user_play_readiness,
)

__all__ = [
    # Badge presentation / liveness.
    "LIVE_WINDOW_SECONDS",
    "_HEARTBEAT_THROTTLE_SECONDS",
    "LOOP_RUNNING_WINDOW_SECONDS",
    "within_window",
    "humanize_since",
    "ConnectionHealth",
    "_HEALTH_PRESENTATION",
    "ConnectionHealthStatus",
    "CalmConnectionStatus",
    "calm_connection_status",
    "agent_is_defaulting",
    "compute_connection_health",
    "_connection_is_live",
    # Provider readiness.
    "MCP_CONNECTION_VALID_DAYS",
    "MCP_CONNECTION_PROVIDERS",
    "_provider_connections_query",
    "provider_is_covered",
    "provider_enabled_on_any_connection",
    "provider_uses_mcp_connection",
    "provider_has_recent_mcp_connection",
    "provider_has_machine_connection",
    "provider_has_current_setup",
    "provider_has_live_current_setup",
    "provider_loop_running",
    "ProviderReadiness",
    "provider_readiness",
    "enabled_provider_values",
    "user_play_readiness",
    # Join-gate capacity.
    "active_matches_for_provider",
    "live_provider_capacity",
    "is_join_blocked",
    "active_matches_for_user",
    "live_user_capacity",
    "providers_busy_for_user",
]
