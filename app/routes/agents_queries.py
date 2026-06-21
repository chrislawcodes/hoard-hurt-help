"""Shared read queries for the /me/agents route family."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from app.deps import DbSession
from app.models.agent import Agent, AgentKind
from app.models.user import User


async def load_owned_agent(db: DbSession, user: User, agent_id: int) -> Agent:
    """Load the user's own, non-archived AI agent, or raise 404.

    Archived agents are hidden from every read page, so write actions
    (rename / pause / strategy / delete) must not be able to load one either.
    This is the single canonical loader the agent routes share.
    """
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
