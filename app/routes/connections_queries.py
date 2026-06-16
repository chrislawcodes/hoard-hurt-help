"""Shared read queries for the connections UI.

Loaders and small derivations used across the connection list, detail, and setup
pages: the user's agents, which agents a machine covers, which are stranded,
provider toggle rows, owned-connection lookups, and the shared "are we live"
context. Kept apart so the page modules stay thin and the same queries can't
drift between the full page and its poll fragments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.deps import DbSession
from app.engine.agent_idle import GameTiming, game_timing_for_user
from app.engine.connection_health import (
    LIVE_WINDOW_SECONDS,
    ConnectionHealth,
    compute_connection_health,
)
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.user import User
from app.routes.connections_connect_guide import _play_prompt
from app.routes.provider_labels import PROVIDER_LABELS


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None


def _connection_display_name(connection: Connection) -> str:
    # A Mode A connection is one MCP client, which speaks for exactly one AI
    # provider — so it is named by that provider (Claude, OpenAI…), never
    # user-nicknamed. (Nicknaming is a machine idea: you name your computer.)
    if connection.mode_a_at:
        if connection.provider is not None:
            return PROVIDER_LABELS.get(
                connection.provider.value, connection.provider.value.title()
            )
        return "MCP connection"
    # A machine connection runs several CLIs at once, so the user names the box.
    if connection.nickname:
        return connection.nickname
    return "Machine connection"


async def _load_user_agents(db: DbSession, user_id: int) -> list[AgentRow]:
    """All of the user's active AI agents (not bots), newest name first, with
    their current version so the readiness line can show name · model."""
    rows = (
        (
            await db.execute(
                select(Agent, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
                .where(
                    Agent.user_id == user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.archived_at.is_(None),
                )
                .order_by(Agent.name)
            )
        )
        .all()
    )
    return [AgentRow(agent=agent, version=version) for agent, version in rows]


async def _load_attached_agents(db: DbSession, connection: Connection) -> list[AgentRow]:
    """Agents this machine COVERS: the user's active AI agents whose provider is
    enabled on this connection (agents are no longer attached to a connection)."""
    enabled = (
        (
            await db.execute(
                select(ConnectionProviderRow.provider).where(
                    ConnectionProviderRow.connection_id == connection.id,
                    ConnectionProviderRow.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not enabled:
        return []
    rows = (
        (
            await db.execute(
                select(Agent, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
                .where(
                    Agent.user_id == connection.user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.archived_at.is_(None),
                    Agent.provider.in_(enabled),
                )
                .order_by(Agent.name)
            )
        )
        .all()
    )
    return [AgentRow(agent=agent, version=version) for agent, version in rows]


async def _load_stranded_agents(db: DbSession, user_id: int) -> list[AgentRow]:
    """Active AI agents whose provider is enabled on NO live connection — they
    are waiting for a machine to come up that covers them."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LIVE_WINDOW_SECONDS)
    live_providers = set(
        (
            await db.execute(
                select(ConnectionProviderRow.provider)
                .join(Connection, Connection.id == ConnectionProviderRow.connection_id)
                .where(
                    ConnectionProviderRow.enabled.is_(True),
                    Connection.user_id == user_id,
                    Connection.deleted_at.is_(None),
                    Connection.status != ConnectionStatus.PAUSED,
                    Connection.last_seen_at.is_not(None),
                    Connection.last_seen_at >= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    rows = (
        (
            await db.execute(
                select(Agent, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
                .where(
                    Agent.user_id == user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.archived_at.is_(None),
                )
                .order_by(Agent.name)
            )
        )
        .all()
    )
    return [
        AgentRow(agent=agent, version=version)
        for agent, version in rows
        if agent.provider not in live_providers
    ]


async def _load_connection_providers(
    db: DbSession, connection_id: int
) -> dict[str, ConnectionProviderRow]:
    """Map provider value → its toggle row for this connection (for the UI box)."""
    rows = (
        (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == connection_id
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.provider.value: row for row in rows}


async def _load_owned_connection(db: DbSession, user: User, connection_id: int) -> Connection:
    connection = (
        await db.execute(
            select(Connection).where(
                Connection.id == connection_id,
                Connection.user_id == user.id,
                Connection.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found.")
    return connection


def _summarize_agent(agents: list[AgentRow]) -> tuple[bool, str | None]:
    """Whether the user has an AI agent and the "name · model" summary of the first."""
    if not agents:
        return False, None
    first = agents[0]
    model = first.version.model if first.version is not None else None
    summary = f"{first.agent.name} · {model}" if model else first.agent.name
    return True, summary


async def _live_status_context(
    db: DbSession,
    user: User,
    *,
    next_url: str | None = None,
    provider: ConnectionProvider | None = None,
) -> dict[str, object]:
    """Shared 'are we live + agent nudge' context for the page and the poll fragment.

    A user is live now if ANY of their non-deleted connections resolves to a LIVE or
    READY health state (running machine, idle-but-ready counts). They are *playing*
    now if such a live connection has made at least one authenticated game call
    (``api_call_count > 0``) — signing in does not bump that counter, so it's the
    proof the play-prompt took and the AI is actually calling the game on its own.
    Reuses the per-row health computation so the page and the 4s poll fragment can't
    drift.

    When ``provider`` is given, only connections that serve THAT provider count — so
    a page opened to connect Gemini reflects Gemini's status, not a live Claude's.
    """
    conn_query = select(Connection).where(
        Connection.user_id == user.id, Connection.deleted_at.is_(None)
    )
    if provider is not None:
        conn_query = conn_query.join(
            ConnectionProviderRow,
            ConnectionProviderRow.connection_id == Connection.id,
        ).where(
            ConnectionProviderRow.provider == provider,
            ConnectionProviderRow.enabled.is_(True),
        )
    connections = (
        (
            await db.execute(
                conn_query.order_by(
                    Connection.created_at.desc(), Connection.id.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    is_live_now = False
    is_playing_now = False
    for connection in connections:
        health = await compute_connection_health(db, connection)
        if health.state in (ConnectionHealth.LIVE, ConnectionHealth.READY):
            is_live_now = True
            if connection.api_call_count > 0:
                is_playing_now = True
                break
    has_agent, agent_summary = _summarize_agent(await _load_user_agents(db, user.id))
    # The free, server-rendered "what's my AI waiting for" line — so the human
    # reads game timing off the page, not off the (paid) AI's narration.
    next_game_status = _next_game_line(await game_timing_for_user(db, user.id))
    return {
        "is_live_now": is_live_now,
        "is_playing_now": is_playing_now,
        "next_game_status": next_game_status,
        "has_agent": has_agent,
        "agent_summary": agent_summary,
        "play_prompt": _play_prompt(),
        "lobby_url": "/games/hoard-hurt-help",
        # When set, the connect→play flow forwards here the moment the AI is live
        # (a join hub sent the user to start their machine). None = stay put.
        "next_url": next_url,
    }


def _next_game_line(timing: GameTiming) -> str:
    """One plain line for the status box: what the running AI is waiting on."""
    if timing.has_live_game:
        return "A game is live now — your AI is playing it."
    seconds = timing.seconds_to_next_start
    if seconds is not None:
        if seconds < 90:
            return "Your next game is starting now."
        return f"Your next game starts in about {round(seconds / 60)} min."
    return "No game yet — join one and your AI will jump in."
