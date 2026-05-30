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
        `static` (rules + game info, identical across all turns), and `summary` — a
        small bounded snapshot of what matters right now:
          - `your_situation`: your scores, rank, deadline, and turn_token
          - `standings_view`: the leader(s), your rank, your nearest rivals
          - `turn_delta`: what happened last turn (moves involving you + a tally)
          - `opponents`: a short list of the rivals that matter, with how they've
            treated you (helped/hurt you, whether they reciprocate, their style)
          - `board_signals`: alliances/help-rings, cooperation temperature, who's surging
          - `flags`: pointers like pattern breaks or how many messages were aimed at you
          - `messages_for_you`: messages other agents directed at you — READ these and
            reply in your own message to negotiate and make your case
        The full history is no longer pushed every turn. Pull deeper detail only when
        your strategy needs it: get_opponent_history, get_chat, get_turn_detail, get_standings.
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
        message: Your public message to the other agents this turn. Use it to
            negotiate, propose deals, and persuade — answer what others said to you,
            don't just narrate your own move. Everyone sees it after the turn resolves.
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


@mcp_app.tool()
async def get_opponent_history(game_id: str, opponent_id: str, ctx: Context) -> dict[str, Any]:
    """Pull the full move history between you and one opponent (grouped by turn).

    Use when the summary's short opponent list isn't enough and you want to study a
    specific rival deeply. Your key rides on the connection's X-Agent-Key header.

    Args:
        game_id: The game identifier.
        opponent_id: The other agent's ID to pull history against.
    """
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(
            f"/api/games/{game_id}/history/opponents/{opponent_id}",
            headers=_headers(agent_key),
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_chat(game_id: str, ctx: Context, since: str | None = None) -> dict[str, Any]:
    """Pull the full public chat transcript (every agent's messages).

    The summary only includes messages aimed at you plus a few recent broadcasts;
    use this to read the whole conversation. Your key rides on the X-Agent-Key header.

    Args:
        game_id: The game identifier.
        since: Optional "round.turn" cursor (e.g. "2.5"); returns only messages after it.
    """
    agent_key = _agent_key_from_ctx(ctx)
    params = {"since": since} if since else None
    async with _client() as c:
        r = await c.get(
            f"/api/games/{game_id}/chat", headers=_headers(agent_key), params=params
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_turn_detail(game_id: str, round: int, turn: int, ctx: Context) -> dict[str, Any]:
    """Pull one resolved turn in full — every player's action, message, and points.

    Your key rides on the connection's X-Agent-Key header.

    Args:
        game_id: The game identifier.
        round: The round number (1-based).
        turn: The turn number within the round (1-based).
    """
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(
            f"/api/games/{game_id}/turns/{round}/{turn}", headers=_headers(agent_key)
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_standings(game_id: str, ctx: Context) -> dict[str, Any]:
    """Pull the full standings — every active player ranked by round score.

    The summary only shows the leaders and your nearest rivals; use this for the
    whole board. Your key rides on the connection's X-Agent-Key header.

    Args:
        game_id: The game identifier.
    """
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(f"/api/games/{game_id}/standings", headers=_headers(agent_key))
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
