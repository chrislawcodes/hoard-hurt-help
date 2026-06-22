"""The `/me/agents` list page — every AI agent the user owns, with health."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from starlette.responses import Response

from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import (
    ConnectionHealth,
    ConnectionHealthStatus,
    ProviderReadiness,
    user_play_readiness,
)
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.user import User
from app.routes.agents_health_presenter import (
    AgentRow,
    _count_agent_matches_for_agents,
    _readiness_state,
    health_view,
)
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
    match_counts = await _count_agent_matches_for_agents(
        db, [agent.id for agent, _ in agents]
    )
    # Agents are provider-agnostic, so readiness is the same for all of them:
    # whether the user has any live connection. Compute it once.
    readiness = await user_play_readiness(db, user.id)
    rows: list[AgentRow] = []
    for agent, version in agents:
        # No per-agent provider any more; the connect CTA is generic.
        provider_label = None
        connect_url = "/me/connections"
        if agent.status == AgentStatus.PAUSED:
            status = ConnectionHealthStatus(
                state=ConnectionHealth.PAUSED,
                label="Paused",
                badge_class="badge-done",
                pulse=False,
                needs_reconnect=False,
                never_connected=False,
                last_connected_at=None,
                last_connected_human=None,
            )
        elif readiness == ProviderReadiness.NO_MCP_CONNECTION:
            # NO_MCP_CONNECTION: no recent MCP setup at all → needs connecting.
            status = ConnectionHealthStatus(
                state=ConnectionHealth.DISCONNECTED,
                label="Needs connecting",
                badge_class="badge-alert",
                pulse=False,
                needs_reconnect=True,
                never_connected=True,
                last_connected_at=None,
                last_connected_human=None,
            )
        else:
            # Any rung above NO_MCP_CONNECTION means the provider has a current
            # MCP setup (CONNECTED_NOT_LIVE / SEEN_NOT_POLLING / LIVE) → ready.
            status = ConnectionHealthStatus(
                state=ConnectionHealth.READY,
                label="Ready",
                badge_class="badge-ok",
                pulse=False,
                needs_reconnect=False,
                never_connected=False,
                last_connected_at=None,
                last_connected_human=None,
            )
        health: object = health_view(status)
        needs_connecting = _readiness_state({"health": health, "join_blocked": False}) == "needs_connecting"
        rows.append(
            AgentRow(
                agent=agent,
                version=version,
                health=health,
                match_count=match_counts.get(agent.id, 0),
                provider_label=provider_label,
                connect_url=connect_url,
                needs_connecting=needs_connecting,
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
