"""Shared read queries for the /me/agents route family."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import Select, select

from app.deps import DbSession
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.user import User


def user_agents_select(user_id: int, *, ai_only: bool) -> Select[tuple[Agent, AgentVersion]]:
    """Base ``select(Agent, AgentVersion)`` for a user's non-archived agents.

    Left-joins each agent to its current version (so version may be ``None``) and
    filters to one user's non-archived agents. When *ai_only* is true it also
    excludes non-AI (bot) agents.

    Callers add their own ``order_by`` and do their own row-wrapping, because the
    three call sites genuinely differ on both: the join screen and the agents
    list order by newest-first and keep raw ``(Agent, version)`` tuples, while the
    connections UI orders by name and wraps rows in its own ``AgentRow``. This
    helper only shares the part that is identical across all three: the base
    select, the version left-join, and the per-user / non-archived filter.
    """
    query = (
        select(Agent, AgentVersion)
        .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
        .where(Agent.user_id == user_id, Agent.archived_at.is_(None))
    )
    if ai_only:
        query = query.where(Agent.kind == AgentKind.AI)
    return query


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
