"""Join-gate capacity: active-match counts vs. live connection capacity.

The top layer of the connection-health surface. Builds on
``provider_readiness`` (the shared connections query) and
``connection_health_badge`` (liveness). Answers "can this user/provider take
another game right now, or is the join gate blocked?".
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.connection_health_badge import _connection_is_live
from app.engine.provider_readiness import _provider_connections_query
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionProvider
from app.models.match import GameState, Match
from app.models.player import Player


async def active_matches_for_provider(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> int:
    """Count active matches for AI agents of *user_id* whose provider is *provider*.

    Used by the SUM join-gate rule.
    """
    count = await db.scalar(
        select(func.count(func.distinct(Match.id)))
        .select_from(Agent)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .where(
            Agent.user_id == user_id,
            Agent.provider == provider,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state == GameState.ACTIVE,
        )
    )
    return int(count or 0)


async def live_provider_capacity(
    db: AsyncSession, user_id: int, provider: ConnectionProvider
) -> int:
    """Sum of max_concurrent_games over the user's live connections that have *provider* enabled.

    Returns 0 when no live connection covers the provider (join is always blocked).
    """
    now = datetime.now(timezone.utc)
    query = _provider_connections_query(
        user_id, provider, Connection, require_mcp_when_provider_uses=True
    )
    rows = (await db.execute(query)).scalars().all()
    return sum(c.max_concurrent_games for c in rows if _connection_is_live(c, now))


def is_join_blocked(active_count: int, capacity_sum: int) -> bool:
    """Return True when the active count reaches or exceeds the combined capacity.

    DB-free helper — unit-testable without a session.
    capacity_sum == 0 means no live connection covers the provider → always blocked.
    """
    return active_count >= capacity_sum if capacity_sum > 0 else True


# ---------------------------------------------------------------------------
# User-level (provider-agnostic) capacity
#
# Agents are no longer tied to a provider: any of a user's live connections can
# serve any of their agents. These reduce the per-provider primitives above over
# *all* the user's connections, so play-setup and the join gate reason about
# "do I have a live AI at all?" rather than "is provider X live?".
# ---------------------------------------------------------------------------


async def active_matches_for_user(db: AsyncSession, user_id: int) -> int:
    """Count active matches across ALL the user's AI agents (provider-agnostic)."""
    count = await db.scalar(
        select(func.count(func.distinct(Match.id)))
        .select_from(Agent)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .where(
            Agent.user_id == user_id,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state == GameState.ACTIVE,
        )
    )
    return int(count or 0)


async def live_user_capacity(db: AsyncSession, user_id: int) -> int:
    """Sum of ``max_concurrent_games`` over the user's live connections (any provider).

    Each connection is counted once. Returns 0 when none are live (join blocked).
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Connection).where(
                Connection.user_id == user_id,
                Connection.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return sum(c.max_concurrent_games for c in rows if _connection_is_live(c, now))


async def providers_busy_for_user(db: AsyncSession, user_id: int) -> dict[str, str]:
    """Map provider value → a match name for AIs already committed to a seat.

    "One AI plays one game at a time" — strictly, one AI fills one seat at a time:
    a provider is busy when it's the chosen AI of ANY of the user's seats in a
    match that hasn't finished, playing now (ACTIVE) or booked upcoming
    (SCHEDULED / REGISTERING), including a seat in the same game. To field several
    agents in one game, pick a different AI for each. The join picker greys busy
    AIs out and the join gate refuses to pick one. Returns the match name so the
    picker can say which game it's in.
    """
    rows = await db.execute(
        select(Player.chosen_provider, Match.name)
        .join(Match, Match.id == Player.match_id)
        .where(
            Player.user_id == user_id,
            Player.left_at.is_(None),
            Player.chosen_provider.is_not(None),
            Match.state.notin_([GameState.COMPLETED, GameState.CANCELLED]),
        )
    )
    busy: dict[str, str] = {}
    for provider_value, match_name in rows.all():
        if provider_value is not None:
            busy.setdefault(provider_value, match_name)
    return busy
