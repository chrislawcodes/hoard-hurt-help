"""Human-player identity: a user's ``kind=human`` agent for a game.

A human seat reuses the existing :class:`Player` → :class:`Agent` shape, the same
way a bot does, so the viewer, scoreboard, history, and exports need no special
casing. A human's agent:

- has ``kind=AgentKind.HUMAN``, no :class:`Connection`, and ``provider=None``
  (a person drives it through the web, not an LLM);
- is created once per ``(user, game)`` and reused across that user's matches,
  mirroring how an AI agent is a per-game competitor identity;
- carries exactly one frozen :class:`AgentVersion` (``model="human"``,
  empty strategy) so every read path that joins ``AgentVersion`` keeps working.

The public in-match label is the player's ``seat_name`` (set at seating time),
not this agent's ``name`` — so the internal name only needs to be stable and
unique among the user's agents.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.user import User

HUMAN_VERSION_MODEL = "human"


async def _unique_agent_name(db: AsyncSession, user_id: int, base: str) -> str:
    """Return a name unique among this user's agents (``base``, ``base 2``, …)."""
    existing = set(
        (
            await db.execute(
                select(Agent.name).where(Agent.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    if base not in existing:
        return base
    suffix = 2
    while f"{base} {suffix}" in existing:
        suffix += 1
    return f"{base} {suffix}"


async def _ensure_frozen_version(db: AsyncSession, agent: Agent) -> AgentVersion:
    """Find or create the agent's single frozen human version and pin it."""
    version = (
        await db.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version_no)
        )
    ).scalar_one_or_none()
    if version is None:
        version = AgentVersion(
            agent_id=agent.id,
            version_no=1,
            model=HUMAN_VERSION_MODEL,
            strategy_text="",
            frozen_at=datetime.now(timezone.utc),
        )
        db.add(version)
        await db.flush()
    if agent.current_version_id != version.id:
        agent.current_version_id = version.id
        await db.flush()
    return version


async def get_or_create_human_agent(
    db: AsyncSession, user: User, game: str
) -> tuple[Agent, AgentVersion]:
    """Return this user's ``kind=human`` agent for ``game`` (creating it once).

    Idempotent: repeated calls return the same agent and its frozen version. Does
    not commit — the caller owns the transaction.
    """
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.user_id == user.id,
                Agent.game == game,
                Agent.kind == AgentKind.HUMAN,
            )
        )
    ).scalar_one_or_none()

    if agent is None:
        base = (user.handle or user.name or f"Player{user.id}").strip()
        name = await _unique_agent_name(db, user.id, base)
        agent = Agent(
            user_id=user.id,
            name=name,
            kind=AgentKind.HUMAN,
            provider=None,
            game=game,
            status=AgentStatus.ACTIVE,
        )
        db.add(agent)
        await db.flush()

    version = await _ensure_frozen_version(db, agent)
    return agent, version
