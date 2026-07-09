"""Shared read queries for the /me/agents route family."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import Select, func, select

from app.deps import DbSession
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match, MatchKind
from app.models.player import Player
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


async def version_has_rated_history(db: DbSession, version_id: int) -> bool:
    """True once a version has been seated in any non-practice (rated) match.

    A rated match freezes the version, so a later edit forks a new version
    instead of overwriting it. Shared by the save path (``agents_lifecycle``) and
    the detail page's fork preview so the two can't drift.
    """
    row = (
        await db.execute(
            select(Player.id)
            .join(Match, Match.id == Player.match_id)
            .where(
                Player.agent_version_id == version_id,
                Match.match_kind != MatchKind.PRACTICE_ARENA.value,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def version_fork_preview(
    db: DbSession, *, agent_id: int, version: AgentVersion
) -> tuple[bool, int]:
    """Preview what saving an edit to *version* would do, for the editor's button.

    Returns ``(will_fork, version_no)``: ``will_fork`` is true when the version is
    frozen or already has rated history (so saving creates a new version), and
    ``version_no`` is the number that save would land on — the next number when
    forking, or the current highest when editing in place.
    """
    max_version_no = await db.scalar(
        select(func.max(AgentVersion.version_no)).where(
            AgentVersion.agent_id == agent_id
        )
    )
    will_fork = version.frozen_at is not None or await version_has_rated_history(
        db, version.id
    )
    next_version_no = (
        int(max_version_no or 0) + 1 if will_fork else int(max_version_no or 1)
    )
    return will_fork, next_version_no
