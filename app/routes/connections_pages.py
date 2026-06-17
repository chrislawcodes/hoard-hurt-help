"""Connection list and detail pages plus their live-status poll fragments.

The human-facing connections surface: the one-box list page, the 4s connect→play
poll, and a single machine's detail page with its status and health-badge
fragments. Read-only views built from the shared queries; the setup-minting and
name-saving actions live in ``connections_machine_setup``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette import status
from starlette.responses import Response

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import (
    ProviderReadiness,
    compute_connection_health,
    provider_readiness,
)
from app.engine.pending_connection_gc import gc_pending_connections
from app.models.connection import Connection, ConnectionProvider
from app.models.user import User
from app.routes.connections_connect_guide import (
    _PROVIDER_CLIS,
    _connect_options,
    _play_prompt,
    _provider_label,
    _setup_message,
)
from app.routes.connections_machine_setup import _ensure_pending_setup_and_key
from app.routes.connections_queries import (
    _connection_display_name,
    _live_status_context,
    _load_attached_agents,
    _load_connection_providers,
    _load_owned_connection,
    _load_stranded_agents,
    _load_user_agents,
    _summarize_agent,
)
from app.routes.web_support import safe_internal_next
from app.templating import templates

router = APIRouter()

_PROVIDER_CLIENT_IDS = {
    ConnectionProvider.CLAUDE.value: "claude-code",
    ConnectionProvider.GEMINI.value: "gemini",
    ConnectionProvider.OPENAI.value: "codex",
}


def _normalized_provider_hint(provider: str | None) -> str | None:
    if provider is None:
        return None
    cleaned = provider.strip().lower()
    if not cleaned:
        return None
    try:
        ConnectionProvider(cleaned)
    except ValueError:
        return None
    return cleaned


def _selected_client_id(provider: str | None) -> str | None:
    normalized = _normalized_provider_hint(provider)
    if normalized is None:
        return None
    return _PROVIDER_CLIENT_IDS.get(normalized)


@router.get("", response_class=HTMLResponse)
async def list_connections(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    next: str | None = None,
    provider: str | None = None,
) -> Response:
    next_url = safe_internal_next(next)
    provider_hint = _normalized_provider_hint(provider)
    selected_client_id = _selected_client_id(provider_hint)
    await gc_pending_connections(db)
    connections = (
        (
            await db.execute(
                select(Connection)
                .where(Connection.user_id == user.id, Connection.deleted_at.is_(None))
                .order_by(Connection.created_at.desc(), Connection.id.desc())
            )
        )
        .scalars()
        .all()
    )
    # The page always offers a ready-to-run setup command inline: reuse the user's
    # one open machine setup or mint it, with a key that stays stable across loads.
    active_setup, key = await _ensure_pending_setup_and_key(request, db, user.id)
    target_provider = (
        ConnectionProvider(provider_hint) if provider_hint is not None else None
    )
    rows = [
        {
            "connection": connection,
            "display_name": _connection_display_name(connection),
            "health": await compute_connection_health(db, connection),
            "agents": await _load_attached_agents(db, connection),
        }
        for connection in connections
    ]
    # Live/playing is scoped to the TARGET provider when one was given, so a page
    # opened to connect Gemini reflects Gemini's status — never a live Claude's.
    # With no target it falls back to "any connection of mine" (per-account).
    status_flags = await _live_status_context(
        db, user, next_url=next_url, provider=target_provider
    )
    is_live_now = bool(status_flags["is_live_now"])
    is_playing_now = bool(status_flags["is_playing_now"])
    # Three user states drive what the one-box leads with (see the design doc):
    #   NEW       — never connected (no connection rows)
    #   RETURNING — connected before, but none live right now
    #   LIVE      — at least one connection is LIVE or READY right now
    has_connected_before = bool(connections)
    has_agent, agent_summary = _summarize_agent(await _load_user_agents(db, user.id))
    target_provider_readiness = (
        await provider_readiness(db, user.id, target_provider)
        if target_provider is not None
        else None
    )
    # "set up at all" → readiness != NO_MCP_CONNECTION
    target_provider_setup = (
        target_provider_readiness is not None
        and target_provider_readiness != ProviderReadiness.NO_MCP_CONNECTION
    )
    # Came to connect a SPECIFIC provider that isn't set up yet → lead with that
    # provider's connect steps. Without this, a different live provider (a live
    # Claude) makes the page read as "you're all set" and bounce the user back.
    connect_target = (
        target_provider is not None
        and not target_provider_setup
    )
    target_provider_label = (
        _provider_label(target_provider) if target_provider is not None else None
    )
    # Hub forward: if a join page sent the user here to start their AI and a
    # connection is already live, jump straight back instead of making them read
    # the "Connected" box. The 4s poll covers the case where it goes live later.
    # Only short-circuit on the TARGET provider (or, with no target, any live
    # connection) — never bounce a Gemini connect just because Claude is live.
    # Decision 4: advance the instant the MCP client is SEEN (SEEN_NOT_POLLING or
    # LIVE), before the first turn poll.
    target_provider_seen = target_provider_readiness in {
        ProviderReadiness.SEEN_NOT_POLLING,
        ProviderReadiness.LIVE,
    }
    if next_url and (
        target_provider_seen or (provider_hint is None and is_live_now)
    ):
        return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "connections/list.html",
        {
            "user": user,
            "connections": rows,
            "active_setup": active_setup,
            "setup_message": _setup_message(key),
            "connect_options": _connect_options(),
            "play_prompt": _play_prompt(),
            "has_connected_before": has_connected_before,
            "is_live_now": is_live_now,
            "is_playing_now": is_playing_now,
            "next_game_status": status_flags["next_game_status"],
            "has_agent": has_agent,
            "agent_summary": agent_summary,
            "lobby_url": "/games/hoard-hurt-help",
            "next_url": next_url,
            "provider_hint": provider_hint,
            "selected_client_id": selected_client_id,
            "connect_target": connect_target,
            "target_provider_label": target_provider_label,
        },
    )


@router.get("/live-status", response_class=HTMLResponse)
async def live_status_fragment(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    next: str | None = None,
    provider: str | None = None,
) -> Response:
    """The self-advancing connect → play region, polled every 4s.

    Three states: not live → the pulsing "Waiting for your AI to connect…" line;
    live but not yet playing → "Connected", leading with the play-prompt code block
    to paste (a "Create an agent" nudge first if the user has no agent); playing →
    a "Your AI is playing" success box once the AI has made a real game call.

    Hub forward: when called with a validated ?next and the AI has just gone live,
    answer the HTMX poll with an HX-Redirect so the browser jumps back to the join
    hub — the same auto-advance the page does on load, but for the poll path.
    """
    next_url = safe_internal_next(next)
    provider_hint = _normalized_provider_hint(provider)
    target_provider = (
        ConnectionProvider(provider_hint) if provider_hint is not None else None
    )
    # Scope the "live / playing" status to the target provider, so the poll on a
    # Connect-Gemini page never flips to "your AI is playing" off a live Claude.
    context = await _live_status_context(
        db, user, next_url=next_url, provider=target_provider
    )
    context["provider_hint"] = provider_hint
    # Decision 4: advance the instant the MCP client is SEEN (SEEN_NOT_POLLING or
    # LIVE), before the first turn poll. Use the same rule as the page-load path.
    poll_provider_seen = (
        target_provider is not None
        and (
            await provider_readiness(db, user.id, target_provider)
        )
        in {ProviderReadiness.SEEN_NOT_POLLING, ProviderReadiness.LIVE}
    )
    if next_url and (
        poll_provider_seen or (provider_hint is None and context["is_live_now"])
    ):
        # HTMX honors HX-Redirect by navigating the whole page. An empty body is
        # fine since the redirect replaces this fragment's container entirely.
        return HTMLResponse("", headers={"HX-Redirect": next_url})
    return templates.TemplateResponse(
        request,
        "connections/_live_status.html",
        context,
    )


@router.get("/{connection_id}", response_class=HTMLResponse)
async def connection_detail(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    connection = await _load_owned_connection(db, user, connection_id)
    fresh_key = request.session.pop(f"fresh_connection_key_{connection.id}", None)
    health = await compute_connection_health(db, connection)
    attached_agents = await _load_attached_agents(db, connection)
    stranded_agents = await _load_stranded_agents(db, user.id)
    provider_rows = await _load_connection_providers(db, connection.id)
    # The toggle box lists every provider with its current enabled/detected state.
    provider_toggles = [
        {
            "value": p.value,
            "label": _provider_label(p),
            "cli": _PROVIDER_CLIS.get(p.value, p.value),
            "enabled": (provider_rows[p.value].enabled if p.value in provider_rows else False),
            "detected": (provider_rows[p.value].detected if p.value in provider_rows else False),
            "detected_detail": (
                provider_rows[p.value].detected_detail if p.value in provider_rows else None
            ),
        }
        for p in ConnectionProvider
    ]
    # An MCP connection is not a machine running several CLIs — the AI
    # client you signed in with speaks for exactly one provider (one client ==
    # one provider, per #392). So it gets a read-only list of the provider(s) it
    # actually plays, not the machine-style multi-provider toggle box.
    is_mcp_connection = connection.mcp_connected_at is not None
    mcp_connection_providers = (
        [{"value": t["value"], "label": t["label"]} for t in provider_toggles if t["enabled"]]
        if is_mcp_connection
        else []
    )
    setup_message = _setup_message(fresh_key) if fresh_key is not None else None
    return templates.TemplateResponse(
        request,
        "connections/detail.html",
        {
            "user": user,
            "connection": connection,
            "display_name": _connection_display_name(connection),
            "health": health,
            "fresh_key": fresh_key,
            "setup_message": setup_message,
            "attached_agents": attached_agents,
            "stranded_agents": stranded_agents,
            "is_mcp_connection": is_mcp_connection,
            "mcp_connection_providers": mcp_connection_providers,
            "provider_toggles": provider_toggles,
            "provider_label": _provider_label(connection.provider),
            "provider_models": (
                PROVIDER_MODELS.get(connection.provider.value, [])
                if connection.provider is not None
                else []
            ),
            "strand_provider": request.query_params.get("strand_provider"),
            "strand_count": request.query_params.get("strand_count"),
            "base_url": settings.base_url,
            "agent_count": len(attached_agents),
        },
    )


@router.get("/{connection_id}/status", response_class=HTMLResponse)
async def connection_status_fragment(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    connection = await _load_owned_connection(db, user, connection_id)
    return templates.TemplateResponse(
        request,
        "connections/_status.html",
        {
            "connection": connection,
            "display_name": _connection_display_name(connection),
            "health": await compute_connection_health(db, connection),
            "agent_count": len(await _load_attached_agents(db, connection)),
        },
    )


@router.get("/{connection_id}/health-badge", response_class=HTMLResponse)
async def connection_health_badge_fragment(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    connection = await _load_owned_connection(db, user, connection_id)
    return templates.TemplateResponse(
        request,
        "connections/_health_badge.html",
        {
            "connection": connection,
            "health": await compute_connection_health(db, connection),
        },
    )
