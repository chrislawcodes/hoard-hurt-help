"""The lobby's onboarding-banner rule: does this user have a warm agent and no game?

The lobby shows a "your agent is connected — join a match" banner when the user
owns an AI agent, has a live/ready connection to serve it, and holds no seat in
any active or upcoming match. Extracted from the lobby handler so the rule lives
in one place, and ordered cheapest-check-first so the page never pays a
per-connection health computation for a user who can't see the banner anyway.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.connection_activity import compute_bot_health
from app.engine.connection_health import LOOP_RUNNING_WINDOW_SECONDS, within_window
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection, ConnectionStatus
from app.models.match import GameState, Match
from app.models.player import Player


async def user_has_warm_agent_without_match(db: AsyncSession, user_id: int) -> bool:
    """True when the user owns an AI agent, a connection is live/ready to serve
    it, and they hold no seat in an active or upcoming match.

    Agents are not attached to a connection: the user has a "warm agent" when
    they own an AI agent and ANY of their connections is live/ready
    (``compute_bot_health`` already reflects provider coverage).
    """
    owns_ai_agent = bool(
        await db.scalar(
            select(func.count())
            .select_from(Agent)
            .where(
                Agent.user_id == user_id,
                Agent.archived_at.is_(None),
                Agent.kind == AgentKind.AI,
            )
        )
    )
    if not owns_ai_agent:
        return False

    connections = (
        (
            await db.execute(
                select(Connection).where(
                    Connection.user_id == user_id,
                    Connection.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    has_warm_connection = False
    for connection in connections:
        # Cheap pre-filter before the per-connection health computation: LIVE
        # and READY both require a non-paused connection whose play-loop
        # heartbeat is warm — the exact first tests compute_bot_health runs.
        # Cold or paused rows can't be live/ready, so skip their DB queries.
        if connection.status == ConnectionStatus.PAUSED:
            continue
        if not within_window(connection.last_polled_at, now, LOOP_RUNNING_WINDOW_SECONDS):
            continue
        health = await compute_bot_health(db, connection, now=now)
        if health.state.value in ("live", "ready"):
            has_warm_connection = True
            break
    if not has_warm_connection:
        return False

    active_entry_count = (
        await db.scalar(
            select(func.count())
            .select_from(Player)
            .join(Match, Player.match_id == Match.id)
            .where(
                Player.user_id == user_id,
                Player.left_at.is_(None),
                Match.state.in_(
                    [GameState.ACTIVE, GameState.SCHEDULED, GameState.REGISTERING]
                ),
            )
        )
    ) or 0
    return active_entry_count == 0
