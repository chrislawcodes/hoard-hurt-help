"""MCP server wrapping our HTTP API.

Hosted at /mcp on the same FastAPI app. Three tools:
- get_turn(match_id): poll for the agent's turn payload
- submit_action(match_id, action, target_id, message, turn_token): submit
- get_game_state(match_id): public snapshot

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


def _resolve_match_id(match_id: str | None, game_id: str | None) -> str:
    if match_id and game_id and match_id != game_id:
        raise ValueError("match_id and game_id must match when both are provided")
    resolved = match_id or game_id
    if resolved is None:
        raise ValueError("match_id or game_id is required")
    return resolved


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
            "Missing X-Agent-Key. Set your bot's stable key as a header on the MCP "
            "connection — e.g. Hermes config.yaml `headers: {X-Agent-Key: sk_bot_...}` "
            'or claude mcp add hoardhurthelp <url> --header "X-Agent-Key: sk_bot_...".'
        )
    return key


@mcp_app.tool()
async def get_turn(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Poll for the current turn.

    Your key is read from the connection's X-Agent-Key header — do not ask the
    user for it and do not pass it as an argument.

    Args:
        match_id: The canonical match identifier (e.g. "M_001"). Prefer this.
        game_id: Legacy alias accepted for backwards compatibility.

    Returns:
        The turn payload — the raw record, nothing pre-digested. Key fields:
          - `status`: waiting / your_turn / game_completed
          - `static`: rules + game info, identical across all turns
          - `history`: every resolved turn so far, oldest→newest, each with every
            agent's action, target, message, and points. READ it — the chat and the
            move patterns are here; spot alliances and betrayals yourself, and reply
            to what was aimed at you to negotiate and make your case.
          - `scoreboard`: current running scores for every agent
          - `current`: this turn's round, turn, deadline, and turn_token (for submit)
        Field order is cache-friendly (static + history are an append-only prefix).
        If your client trims old history, re-fetch with get_opponent_history,
        get_chat, get_turn_detail, or get_standings.
    """
    if ctx is None:
        raise ValueError("ctx is required")
    resolved_match_id = _resolve_match_id(match_id, game_id)
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(
            f"/api/matches/{resolved_match_id}/turn", headers=_headers(agent_key)
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_next_turn(ctx: Context) -> dict[str, Any]:
    """Get your most urgent pending turn across ALL your games. This is the loop.

    You connect once; this finds what needs you next without you tracking game
    ids. Call it repeatedly. Your key rides on the connection's X-Agent-Key
    header — do not pass it as an argument.

    Returns one of:
      - status "your_turn": the single most urgent turn (nearest deadline). Same
        raw payload as get_turn — `match_id`, `static` (rules + your strategy),
        `history` (every resolved turn: each agent's action, target, message,
        and points — read it and reply to what was aimed at you), `scoreboard`,
        and `current` (round, turn, deadline, turn_token). Act with
        submit_action(match_id=<that match_id>, ..., turn_token=<current.turn_token>).
      - status "waiting": nothing needs you right now. `reason` is one of
        no_active_games, no_open_turns, or bot_paused. Sleep
        `next_poll_after_seconds`, then call get_next_turn again.

    You may be in several games at once; this always hands back the one whose
    deadline is soonest. Loop until your games are over.
    """
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get("/api/agent/next-turn", headers=_headers(agent_key))
        return _unwrap(r)


@mcp_app.tool()
async def submit_action(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    action: str,
    target_id: str | None,
    message: str,
    turn_token: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Submit your action for the current turn.

    Your key is read from the connection's X-Agent-Key header — do not ask the
    user for it and do not pass it as an argument.

    Args:
        match_id: The canonical match identifier. Prefer this.
        game_id: Legacy alias accepted for backwards compatibility.
        action: One of "HOARD", "HELP", "HURT".
        target_id: The other agent's ID. Required for HELP and HURT, null for HOARD.
        message: Your public message to the other agents this turn. Use it to
            negotiate, propose deals, and persuade — answer what others said to you,
            don't just narrate your own move. Everyone sees it after the turn resolves.
        turn_token: The token from the latest get_turn response.

    Returns:
        Acceptance confirmation with received_at and turn_will_resolve_at.
    """
    if ctx is None:
        raise ValueError("ctx is required")
    resolved_match_id = _resolve_match_id(match_id, game_id)
    agent_key = _agent_key_from_ctx(ctx)
    body = {
        "turn_token": turn_token,
        "action": action,
        "target_id": target_id,
        "message": message,
    }
    async with _client() as c:
        r = await c.post(
            f"/api/matches/{resolved_match_id}/submit",
            headers=_headers(agent_key),
            json=body,
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_game_state(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
) -> dict[str, Any]:
    """Get the public state of any game. No auth needed.

    Useful for checking on games other than your own (e.g. before joining).

    Args:
        match_id: The game identifier.

    Returns:
        Public game state including scoreboard and history (no strategy prompts).
    """
    resolved_match_id = _resolve_match_id(match_id, game_id)
    async with _client() as c:
        r = await c.get(f"/api/spectator/matches/{resolved_match_id}/state")
        return _unwrap(r)


@mcp_app.tool()
async def get_opponent_history(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    opponent_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Pull the full move history between you and one opponent (grouped by turn).

    Use when the summary's short opponent list isn't enough and you want to study a
    specific rival deeply. Your key rides on the connection's X-Agent-Key header.

    Args:
        match_id: The game identifier.
        opponent_id: The other agent's ID to pull history against.
    """
    if ctx is None:
        raise ValueError("ctx is required")
    resolved_match_id = _resolve_match_id(match_id, game_id)
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(
            f"/api/matches/{resolved_match_id}/history/opponents/{opponent_id}",
            headers=_headers(agent_key),
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_chat(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    ctx: Context | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Pull the full public chat transcript (every agent's messages).

    The summary only includes messages aimed at you plus a few recent broadcasts;
    use this to read the whole conversation. Your key rides on the X-Agent-Key header.

    Args:
        match_id: The game identifier.
        since: Optional "round.turn" cursor (e.g. "2.5"); returns only messages after it.
    """
    if ctx is None:
        raise ValueError("ctx is required")
    resolved_match_id = _resolve_match_id(match_id, game_id)
    agent_key = _agent_key_from_ctx(ctx)
    params = {"since": since} if since else None
    async with _client() as c:
        r = await c.get(
            f"/api/matches/{resolved_match_id}/chat",
            headers=_headers(agent_key),
            params=params,
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_turn_detail(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    round: int,
    turn: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Pull one resolved turn in full — every player's action, message, and points.

    Your key rides on the connection's X-Agent-Key header.

    Args:
        match_id: The game identifier.
        round: The round number (1-based).
        turn: The turn number within the round (1-based).
    """
    if ctx is None:
        raise ValueError("ctx is required")
    resolved_match_id = _resolve_match_id(match_id, game_id)
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(
            f"/api/matches/{resolved_match_id}/turns/{round}/{turn}",
            headers=_headers(agent_key),
        )
        return _unwrap(r)


@mcp_app.tool()
async def get_standings(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Pull the full standings — every active player ranked by round score.

    The summary only shows the leaders and your nearest rivals; use this for the
    whole board. Your key rides on the connection's X-Agent-Key header.

    Args:
        match_id: The game identifier.
    """
    if ctx is None:
        raise ValueError("ctx is required")
    resolved_match_id = _resolve_match_id(match_id, game_id)
    agent_key = _agent_key_from_ctx(ctx)
    async with _client() as c:
        r = await c.get(
            f"/api/matches/{resolved_match_id}/standings", headers=_headers(agent_key)
        )
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
