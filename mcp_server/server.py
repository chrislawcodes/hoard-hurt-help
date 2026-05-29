"""MCP server wrapping our HTTP API.

Hosted at /mcp on the same FastAPI app. Three tools:
- get_turn(game_id): poll for the agent's turn payload
- submit_action(game_id, action, target_id, message, turn_token): submit
- get_game_state(game_id): public snapshot

Auth: the player sets the `X-Agent-Key` header on the MCP connection itself
(Hermes `config.yaml` `headers:`, `claude mcp add --header`, etc.). The
authenticated tools read that header off each request's context, so the key
stays in the client config and never has to appear in the chat prompt.
"""

from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings

# streamable_http_path="/" so that when app.main mounts this whole app at
# "/mcp", the real endpoint is exactly "/mcp" (not "/mcp/mcp", which is what
# the default inner path of "/mcp" would produce under the mount).
#
# transport_security: FastMCP's host defaults to 127.0.0.1, which makes it
# auto-enable localhost-only DNS-rebinding protection. Served on a public
# domain behind Railway's TLS proxy that rejects every real request with
# 421 "Invalid Host header". We authenticate each tool call with X-Agent-Key
# and clients connect by hostname (incl. future custom domains), so disable the
# Host/Origin check rather than pin an allow-list. (Content-Type is still validated.)
mcp_app = FastMCP(
    "hoardhurthelp",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _client() -> httpx.AsyncClient:
    """HTTP client pre-configured to hit our own API."""
    return httpx.AsyncClient(
        base_url=settings.base_url,
        timeout=httpx.Timeout(30.0),
    )


def _headers(agent_key: str) -> dict[str, str]:
    return {"X-Agent-Key": agent_key, "Content-Type": "application/json"}


def _agent_key_from_ctx(ctx: Context) -> str:
    """Pull the per-game key off the MCP connection's HTTP headers.

    For the streamable-HTTP transport the SDK sets `request_context.request`
    to the Starlette request for each tool-call POST, so the `X-Agent-Key`
    header the client was configured with rides along on every call.
    """
    request = getattr(ctx.request_context, "request", None)
    key = request.headers.get("x-agent-key") if request is not None else None
    if not key:
        raise RuntimeError(
            "Missing X-Agent-Key. Set it as a header on the MCP connection — e.g. "
            "Hermes config.yaml `headers: {X-Agent-Key: sk_game_...}` or "
            'claude mcp add hoardhurthelp <url> --header "X-Agent-Key: sk_game_...".'
        )
    return key


@mcp_app.tool()
async def get_turn(game_id: str, ctx: Context) -> dict[str, Any]:
    """Poll for the current turn.

    Your key is read from the connection's X-Agent-Key header — do not ask the
    user for it and do not pass it as an argument.

    Args:
        game_id: The game identifier (e.g. "G_001").

    Returns:
        The turn payload. Key fields: `status` (waiting / your_turn / game_completed),
        `static` (rules + game info, identical across all turns), `dynamic` (scoreboard,
        history, deadline, turn_token).
    """
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(f"/api/games/{game_id}/turn", headers=_headers(agent_key))
        return _unwrap(r)


@mcp_app.tool()
async def submit_action(
    game_id: str,
    action: str,
    target_id: str | None,
    message: str,
    turn_token: str,
    ctx: Context,
) -> dict[str, Any]:
    """Submit your action for the current turn.

    Your key is read from the connection's X-Agent-Key header — do not ask the
    user for it and do not pass it as an argument.

    Args:
        game_id: The game identifier.
        action: One of "HOARD", "HELP", "HURT".
        target_id: The other agent's ID. Required for HELP and HURT, null for HOARD.
        message: Your public message to other agents this turn.
        turn_token: The token from the latest get_turn response.

    Returns:
        Acceptance confirmation with received_at and turn_will_resolve_at.
    """
    agent_key = _agent_key_from_ctx(ctx)
    body = {
        "turn_token": turn_token,
        "action": action,
        "target_id": target_id,
        "message": message,
    }
    async with _client() as c:
        r = await c.post(
            f"/api/games/{game_id}/submit", headers=_headers(agent_key), json=body
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_game_state(game_id: str) -> dict[str, Any]:
    """Get the public state of any game. No auth needed.

    Useful for checking on games other than your own (e.g. before joining).

    Args:
        game_id: The game identifier.

    Returns:
        Public game state including scoreboard and history (no strategy prompts).
    """
    async with _client() as c:
        r = await c.get(f"/api/spectator/games/{game_id}/state")
        return _unwrap(r)


def _unwrap(r: httpx.Response) -> dict[str, Any]:
    """Return JSON body, or raise the error envelope as an MCP-visible error."""
    try:
        data = r.json()
    except Exception:
        r.raise_for_status()
        return {}
    if r.is_success:
        return data
    # FastAPI nests our error envelope under 'detail'.
    err = data.get("detail", data)
    raise RuntimeError(
        f"HTTP {r.status_code}: {err.get('error', err) if isinstance(err, dict) else err}"
    )


# The FastAPI app mounts this at /mcp via mcp_app.streamable_http_app()
asgi_app = mcp_app.streamable_http_app()
