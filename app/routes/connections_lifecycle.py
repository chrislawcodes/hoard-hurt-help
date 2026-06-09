"""Connection pause/resume/delete and agent reattach actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from app.config import PROVIDER_MODELS
from app.deps import DbSession, require_user_with_handle
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionStatus
from app.models.connection_setup import ConnectionSetup
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
    # Deleting a connection must also stop the runner. Removing the connection
    # row marks it deleted, which makes the next runner check-in return a
    # dedicated shutdown response.
    now = datetime.now(timezone.utc)
    connection.deleted_at = now
    connection.status = ConnectionStatus.PAUSED
    connection.paused_at = now
    connection.paused_reason = "deleted"
    connection.runner_pid = None
    connection.prev_key_lookup = None
    # Detach (never delete) this connection's AI agents in one atomic statement
    # scoped to the owned connection: they survive, paused, reattachable (FR-029).
    await db.execute(
        update(Agent)
        .where(
            Agent.connection_id == connection.id,
            Agent.kind == AgentKind.AI,
        )
        .values(connection_id=None, status=AgentStatus.PAUSED)
    )
    await db.execute(
        update(ConnectionSetup)
        .where(ConnectionSetup.connection_id == connection.id)
        .values(connection_id=None)
    )
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
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Connection is not active.")
    agent = await _load_owned_agent(db, user, agent_id)
    if agent.kind != AgentKind.AI:
        raise HTTPException(status_code=400, detail="Only AI agents can be reattached.")
    if agent.connection_id is not None:
        raise HTTPException(status_code=409, detail="That agent already has a connection.")
    if agent.status != AgentStatus.PAUSED:
        raise HTTPException(status_code=409, detail="That agent is not waiting for a connection.")
    model = await _agent_current_model(db, agent.id)
    if model is None:
        raise HTTPException(status_code=400, detail="That agent has no model set.")
    allowed_models = PROVIDER_MODELS.get(connection.provider.value, [])
    # Empty allowed_models (hermes/openclaw) means "any model" — skip the
    # membership check rather than rejecting every reattach against an empty list.
    if allowed_models and model not in allowed_models:
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
