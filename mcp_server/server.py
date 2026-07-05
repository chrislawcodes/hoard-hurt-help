"""MCP server for Hoard Hurt Help — the thin assembly point.

The MCP layer uses Google OAuth, resolves the signed-in user to the canonical
MCP connection, and calls the shared play service in-process. This file used to
hold all four of those jobs in one ~800-line module; they now live in focused
siblings and this file only *wires them together*:

- ``oauth_auth``         — OAuth proxy + JWT decode (concern 1)
- ``connection_identity``— token → connection/player resolution (concern 2)
- ``signin_middleware``  — the initialize-time connection bootstrap (concern 3)
- ``mcp_tools``          — the 7 MCP tools + their instruction text (concern 4)

The assembly here is deliberately small and order-sensitive: build the auth
provider, construct the one ``FastMCP`` app, install the middleware, register the
tools, then build the mounted ASGI app. The DELICATE bits the MCP server has
historically broken on — the stateless-HTTP mount, the auth provider, the
lifespan — stay exactly as they were.

To keep external imports stable, this module re-exposes the public names the rest
of the codebase reaches for via ``mcp_server.server`` (the ``app.main`` mount, the
test suite). Nothing imports the siblings directly today; everything still goes
through ``server``.
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_server.oauth_auth import (
    _MCP_ACCESS_TOKEN_TTL_SECONDS,
    _ConnectAtSignInGoogleProvider,
    _bootstrap_signin_connection_from_idp,
    _build_auth_provider,
    _build_client_storage,
    _decode_jwt_claims,
    _decode_unverified_jwt_payload,
    _sync_signin_user,
    _userinfo_from_claims,
)
from mcp_server.connection_identity import (
    _bootstrap_signin_connection,
    _client_provider_from_initialize,
    _connection_from_token,
    _dcr_client_id_from_request,
    _google_userinfo_from_token,
    _require_access_token,
    _resolve_oauth_connection,
    _resolve_oauth_player,
)
from mcp_server.signin_middleware import SigninConnectionMiddleware
from mcp_server.mcp_tools import (
    _LAST_PULL,
    _NEXT_TURN_HOLD_SECONDS,
    _format_instruction_sections,
    _lean_payload_for_mcp,
    _mcp_how_to_play_block,
    _resolve_match_id,
    _session_scope,
    get_chat,
    get_game_state,
    get_instructions,
    get_next_turn,
    get_next_turns,
    register_tools,
    submit_action,
    submit_talk,
)

# Build the single MCP app, then wire the pieces onto it in order: middleware
# first (it runs on the initialize handshake), then the 7 tools.
mcp_app = FastMCP(
    "agentludum",
    auth=_build_auth_provider(),
)
mcp_app.add_middleware(SigninConnectionMiddleware())
register_tools(mcp_app)


# The parent FastAPI app mounts this at the public root so the auth discovery
# URLs stay rooted at `/.well-known/...` while the MCP endpoint itself remains
# `/mcp`.
#
# stateless_http=True: do NOT keep per-client session state in process memory.
# A stateful server hands each client an Mcp-Session-Id and tracks it in RAM, so
# every redeploy (Railway rolling-deploys on each merge) wipes that map and every
# connected client's next call fails with "Session not found" until the human
# manually reconnects — silently dropping active players mid-game. We don't use
# the features that statefulness buys (server-initiated SSE notifications): play
# is poll-based request/response, the long-poll lives inside a single request, and
# auth is per-call via a reference token. Going stateless makes each request
# self-contained, so a restart can't orphan a client.
asgi_app = mcp_app.http_app(
    path="/mcp", transport="streamable-http", stateless_http=True
)


# Public surface re-exported through ``mcp_server.server`` so importers (the
# app.main mount and the test suite) keep their existing paths. Listing the names
# here documents the contract and tells linters these re-imports are intentional.
__all__ = [
    "mcp_app",
    "asgi_app",
    "register_tools",
    "SigninConnectionMiddleware",
    # OAuth / JWT plumbing (mcp_server.oauth_auth)
    "_MCP_ACCESS_TOKEN_TTL_SECONDS",
    "_ConnectAtSignInGoogleProvider",
    "_bootstrap_signin_connection_from_idp",
    "_build_auth_provider",
    "_build_client_storage",
    "_decode_jwt_claims",
    "_decode_unverified_jwt_payload",
    "_sync_signin_user",
    "_userinfo_from_claims",
    # Connection / player identity (mcp_server.connection_identity)
    "_bootstrap_signin_connection",
    "_client_provider_from_initialize",
    "_connection_from_token",
    "_dcr_client_id_from_request",
    "_google_userinfo_from_token",
    "_require_access_token",
    "_resolve_oauth_connection",
    "_resolve_oauth_player",
    # Tools + instruction text + DB session (mcp_server.mcp_tools)
    "_LAST_PULL",
    "_NEXT_TURN_HOLD_SECONDS",
    "_format_instruction_sections",
    "_lean_payload_for_mcp",
    "_mcp_how_to_play_block",
    "_resolve_match_id",
    "_session_scope",
    "get_chat",
    "get_game_state",
    "get_instructions",
    "get_next_turn",
    "get_next_turns",
    "submit_action",
    "submit_talk",
]
