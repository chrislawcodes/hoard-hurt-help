"""Shared presentation helpers for the agent list and detail pages.

Holds the small value objects the agent templates read, the per-agent match
count query both pages need, and the readiness check that decides whether an
agent can accept a new match invitation. Kept separate so the list, create,
and detail route modules can share it without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from app.deps import DbSession
from app.engine.connection_health import ConnectionHealth
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.match import GameState
from app.models.player import Player


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None
    health: object
    match_count: int


@dataclass(frozen=True)
class VersionRow:
    version: AgentVersion
    rank: int
    match_count: int
    last_played_at: datetime | None
    frozen: bool


@dataclass(frozen=True)
class MatchEntry:
    """One row in the agent-detail matches table."""

    match_id: str
    match_name: str
    game_type: str
    state: GameState
    player_id: int
    round_score: int
    total_score: int
    pre_game: bool


async def _count_agent_matches(db: DbSession, agent_id: int) -> int:
    count = await db.scalar(
        select(func.count()).select_from(Player).where(Player.agent_id == agent_id)
    )
    return int(count or 0)


def _is_ready_to_play(context: dict[str, object]) -> bool:
    """True when the agent can accept a new match invitation right now."""
    health = context.get("health")
    if health is None:
        return False
    if isinstance(health, dict):
        state = health.get("state")
    else:
        state = getattr(health, "state", None)
    if state not in (ConnectionHealth.LIVE, ConnectionHealth.READY):
        return False
    if context.get("join_blocked"):
        return False
    return True
