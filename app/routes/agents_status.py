"""Live status fragments for agent onboarding and detail pages."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.responses import Response

from app.deps import DbSession, require_user_with_handle
from app.engine.agent_onboarding import compute_agent_onboarding_state
from app.engine.connection_health import ConnectionHealth
from app.models.user import User
from app.routes.agents_queries import load_owned_agent
from app.routes.agents_setup import (
    _build_agent_detail_context,
    _is_ready_to_play,
    _load_agent_matches,
)
from app.routes.sse import sse_response
from app.templating import templates

router = APIRouter()


@router.get("/{agent_id}/status", response_class=HTMLResponse)
async def agent_status_fragment(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    """Polled onboarding card fragment — replaces the ready-to-play slot live."""
    agent = await load_owned_agent(db, user, agent_id)
    context = await _build_agent_detail_context(db, request, user, agent)
    matches = await _load_agent_matches(db, agent_id)
    # Coverage-based: pass a non-None sentinel when the provider is live-covered.
    health = context.get("health")
    _health_state = (
        health.get("state") if isinstance(health, dict) else getattr(health, "state", None)
    )
    first_connected_at: object = (
        True
        if _health_state in (ConnectionHealth.READY, ConnectionHealth.LIVE)
        else None
    )
    onboarding = await compute_agent_onboarding_state(
        db,
        agent_id=agent_id,
        first_connected_at=first_connected_at,
        matches=list(matches),
    )
    join_blocked: object = context.get("join_blocked", False)
    ready_to_play = _is_ready_to_play(context)
    capacity_sum: object = context.get("capacity_sum", 0)
    active_match_count: object = context.get("active_match_count", 0)
    return templates.TemplateResponse(
        request,
        "agents/_onboarding.html",
        {
            "agent": agent,
            "onboarding": onboarding,
            "join_blocked": join_blocked,
            "ready_to_play": ready_to_play,
            "active_match_count": active_match_count,
            "capacity_sum": capacity_sum,
        },
    )


@router.get("/{agent_id}/health-badge", response_class=HTMLResponse)
async def agent_health_badge_fragment(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agent = await load_owned_agent(db, user, agent_id)
    context = await _build_agent_detail_context(db, request, user, agent)
    return templates.TemplateResponse(request, "agents/_status.html", context)


@router.get("/{agent_id}/stream")
async def agent_stream(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> StreamingResponse:
    await load_owned_agent(db, user, agent_id)
    return sse_response(f"bot:{agent_id}")
