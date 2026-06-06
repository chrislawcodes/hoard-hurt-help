"""Connection pause/resume/delete and agent reattach actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import PROVIDER_MODELS
from app.deps import DbSession, require_user_with_handle
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionStatus
from app.models.user import User

from app.routes.connections_setup import _load_owned_connection

router = APIRouter()


async def _load_owned_agent(db: DbSession, user: User, agent_id: int) -> Agent:
    agent = (
        await db.execute(
            select(Agent).where(Agent.id == agent_id, Agent.user_id == user.id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


async def _agent_current_model(db: DbSession, agent_id: int) -> str | None:
    row = (
        await db.execute(
            select(AgentVersion.model)
            .select_from(Agent)
            .join(AgentVersion, Agent.current_version_id == AgentVersion.id)
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    return row


@router.post("/{connection_id}/pause")
async def pause_connection(
    connection_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    connection.status = ConnectionStatus.PAUSED
    connection.paused_at = datetime.now(timezone.utc)
    connection.paused_reason = "owner"
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{connection_id}/resume")
async def resume_connection(
    connection_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    connection.status = ConnectionStatus.ACTIVE
    connection.paused_at = None
    connection.paused_reason = None
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{connection_id}/delete")
async def delete_connection(
    connection_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    agents = (
        (
            await db.execute(
                select(Agent).where(Agent.connection_id == connection.id)
            )
        )
        .scalars()
        .all()
    )
    for agent in agents:
        if agent.kind != AgentKind.AI:
            continue
        agent.connection_id = None
        agent.status = AgentStatus.PAUSED
    await db.delete(connection)
    await db.commit()
    return RedirectResponse(url="/me/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{connection_id}/reattach/{agent_id}")
async def reattach_agent(
    connection_id: Annotated[int, Path()],
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    if connection.status == ConnectionStatus.PAUSED:
        raise HTTPException(status_code=409, detail="Connection is paused.")
    agent = await _load_owned_agent(db, user, agent_id)
    if agent.kind != AgentKind.AI:
        raise HTTPException(status_code=400, detail="Only AI agents can be reattached.")
    if agent.connection_id is not None:
        raise HTTPException(status_code=409, detail="That agent already has a connection.")
    model = await _agent_current_model(db, agent.id)
    allowed_models = PROVIDER_MODELS.get(connection.provider.value, [])
    if model is None or model not in allowed_models:
        raise HTTPException(
            status_code=400,
            detail="That agent's model is not valid for this connection provider.",
        )
    agent.connection_id = connection.id
    agent.status = AgentStatus.ACTIVE
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )
