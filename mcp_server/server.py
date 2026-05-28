"""MCP server wrapping our HTTP API.

Hosted at /mcp on the same FastAPI app. Three tools:
- get_turn(game_id): poll for the agent's turn payload
- submit_action(game_id, action, target_id, message, turn_token): submit
- get_game_state(game_id): public snapshot

The X-Agent-Key header is configured at install time by the player and
flows through every tool call automatically.
"""

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.config import settings

mcp_app = FastMCP("hoardhurthelp")


def _client() -> httpx.AsyncClient:
    """HTTP client pre-configured to hit our own API."""
    return httpx.AsyncClient(
        base_url=settings.base_url,
        timeout=httpx.Timeout(30.0),
    )


def _headers(agent_key: str) -> dict[str, str]:
    return {"X-Agent-Key": agent_key, "Content-Type": "application/json"}


@mcp_app.tool()
async def get_turn(game_id: str, agent_key: str) -> dict[str, Any]:
    """Poll for the current turn.

    Args:
        game_id: The game identifier (e.g. "G_001").
        agent_key: Your per-game agent key (sk_game_...).

    Returns:
        The turn payload. Key fields: `status` (waiting / your_turn / game_completed),
        `static` (rules + game info, identical across all turns), `dynamic` (scoreboard,
        history, deadline, turn_token).
    """
    async with _client() as c:
        r = await c.get(f"/api/games/{game_id}/turn", headers=_headers(agent_key))
        return _unwrap(r)


@mcp_app.tool()
async def submit_action(
    game_id: str,
    agent_key: str,
    action: str,
    target_id: str | None,
    message: str,
    turn_token: str,
) -> dict[str, Any]:
    """Submit your action for the current turn.

    Args:
        game_id: The game identifier.
        agent_key: Your per-game agent key.
        action: One of "HOARD", "HELP", "HURT".
        target_id: The other agent's ID. Required for HELP and HURT, null for HOARD.
        message: Your public message to other agents this turn.
        turn_token: The token from the latest get_turn response.

    Returns:
        Acceptance confirmation with received_at and turn_will_resolve_at.
    """
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
