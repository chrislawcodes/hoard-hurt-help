"""The `/me/agents` list page — every AI agent the user owns, with health."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from starlette.responses import Response

from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import ConnectionHealth, provider_is_covered
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.user import User
from app.routes.agents_health_presenter import AgentRow, _count_agent_matches
from app.templating import templates

router = APIRouter()


async def _load_user_agents(db: DbSession, user_id: int) -> list[tuple[Agent, AgentVersion | None]]:
    rows = (
        await db.execute(
            select(Agent, AgentVersion)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(
                Agent.user_id == user_id,
                Agent.kind == AgentKind.AI,
                Agent.archived_at.is_(None),
            )
            .order_by(Agent.created_at.desc(), Agent.id.desc())
        )
    ).all()
    return [(agent, version) for agent, version in rows]


@router.get("", response_class=HTMLResponse)
async def list_agents(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agents = await _load_user_agents(db, user.id)
    rows: list[AgentRow] = []
    for agent, version in agents:
        provider = agent.provider
        covered = (
            await provider_is_covered(db, user.id, provider)
            if provider is not None
            else False
        )
        if agent.status == AgentStatus.PAUSED:
            health: object = {
                "state": ConnectionHealth.PAUSED,
                "label": "Paused",
                "badge_class": "badge-done",
                "pulse": False,
                "needs_reconnect": False,
                "never_connected": False,
                "last_connected_at": None,
                "last_connected_human": None,
                "match_id": None,
                "game_name": None,
                "agent_count": 0,
            }
        elif not covered:
            health = {
                "state": ConnectionHealth.DISCONNECTED,
                "label": "No live connection",
                "badge_class": "badge-alert",
                "pulse": False,
                "needs_reconnect": True,
                "never_connected": True,
                "last_connected_at": None,
                "last_connected_human": None,
                "match_id": None,
                "game_name": None,
                "agent_count": 0,
            }
        else:
            health = {
                "state": ConnectionHealth.READY,
                "label": "Ready",
                "badge_class": "badge-ok",
                "pulse": False,
                "needs_reconnect": False,
                "never_connected": False,
                "last_connected_at": None,
                "last_connected_human": None,
                "match_id": None,
                "game_name": None,
                "agent_count": 0,
            }
        rows.append(
            AgentRow(
                agent=agent,
                version=version,
                health=health,
                match_count=await _count_agent_matches(db, agent.id),
            )
        )
    return templates.TemplateResponse(
        request,
        "agents/list.html",
        {
            "user": user,
            "agents": rows,
        },
    )
