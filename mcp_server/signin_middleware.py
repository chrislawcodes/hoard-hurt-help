"""ASGI/MCP middleware that bootstraps a connection at session start.

This module owns concern #3 of the MCP layer: a single FastMCP middleware that
fires on the ``initialize`` handshake so the /me/connections page flips to
"connected" the instant a client signs in — instead of only after its first
tool call.

It reads its collaborators through the modules that own them:
``get_access_token`` is a module global here (tests patch it on this module),
while ``_bootstrap_signin_connection`` and ``_client_provider_from_initialize``
are read off the ``connection_identity`` module object so a monkeypatch on that
module is observed at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from mcp_server import connection_identity

logger = logging.getLogger(__name__)


class SigninConnectionMiddleware(Middleware):
    """Bootstrap the MCP connection when a client initializes a session."""

    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        token = get_access_token()
        if token is not None:
            try:
                provider = connection_identity._client_provider_from_initialize(
                    context.message
                )
                await connection_identity._bootstrap_signin_connection(token, provider)
            except Exception:
                # fail-open: advisory only — the first tool call still creates and
                # records the connection (see _bootstrap_signin_connection).
                logger.warning(
                    "sign-in connection bootstrap failed; the connection will be "
                    "created on the first tool call instead",
                    exc_info=True,
                )
        return await call_next(context)
