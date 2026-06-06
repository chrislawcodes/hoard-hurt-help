"""Live status fragments for agent onboarding and detail pages."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from starlette.responses import Response

from app.broadcast import subscribe
from app.deps import DbSession, require_user_with_handle
from app.models.agent import Agent, AgentKind
from app.models.user import User
from app.routes.agents_setup import _build_agent_detail_context
from app.templating import templates

router = APIRouter()


async def _load_owned_agent(db: DbSession, user: User, agent_id: int) -> Agent:
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.user_id == user.id,
                Agent.kind == AgentKind.AI,
                Agent.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


@router.get("/{agent_id}/status", response_class=HTMLResponse)
async def agent_status_fragment(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agent = await _load_owned_agent(db, user, agent_id)
    context = await _build_agent_detail_context(db, request, user, agent)
    return templates.TemplateResponse(request, "agents/_status.html", context)


@router.get("/{agent_id}/health-badge", response_class=HTMLResponse)
async def agent_health_badge_fragment(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agent = await _load_owned_agent(db, user, agent_id)
    context = await _build_agent_detail_context(db, request, user, agent)
    return templates.TemplateResponse(request, "agents/_status.html", context)


@router.get("/{agent_id}/stream")
async def agent_stream(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> StreamingResponse:
    await _load_owned_agent(db, user, agent_id)

    async def event_gen() -> AsyncIterator[str]:
        async for msg in subscribe(f"bot:{agent_id}"):
            yield msg

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
