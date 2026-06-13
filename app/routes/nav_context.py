"""Smart "Play" call-to-action for the nav and the marketing hero.

One control, one destination — `/play`, which smart-redirects each visitor to
their real next step — but the label adapts to where they are in the funnel:

* not signed in              -> "Get started"
* signed in, no connection   -> "Connect your AI"
* signed in, connection only -> "Create an Agent"
* signed in, agent connected -> "Play now"

The label depends on the visitor's agent state, which is a DB read, so it can't
live in a (synchronous) Jinja context processor. Instead `populate_nav_cta` runs
as a router dependency, computes the CTA, and stashes it on ``request.state``;
``app.templating`` reads it back into every page's template context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSession, get_current_user
from app.engine.connection_health import LIVE_WINDOW_SECONDS
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.user import User


@dataclass(frozen=True)
class NavCta:
    """The single Play button: a label and where it points."""

    label: str
    href: str


async def user_has_connected_agent(db: AsyncSession, user_id: int) -> bool:
    """True if the user owns a real (non-bot) agent that has connected at least once.

    "Connected once" (``first_connected_at`` is set) — not "alive right now" — is
    the right signal here: once an agent has connected it can play, so the honest
    next step is "Play now". Using live presence would flip the label back to
    "Connect your AI" every time the runner briefly drops, which is wrong.

    Agents are no longer attached to a connection: an agent is "connected" when
    its provider is enabled on one of the user's connections that has connected
    at least once.
    """
    stmt = (
        select(func.count())
        .select_from(Agent)
        .join(ConnectionProviderRow, ConnectionProviderRow.provider == Agent.provider)
        .join(Connection, Connection.id == ConnectionProviderRow.connection_id)
        .where(
            Agent.user_id == user_id,
            Agent.archived_at.is_(None),
            Agent.kind == AgentKind.AI,
            ConnectionProviderRow.enabled.is_(True),
            Connection.user_id == user_id,
            Connection.deleted_at.is_(None),
            Connection.first_connected_at.is_not(None),
        )
    )
    return bool(await db.scalar(stmt))


async def user_connection_count(db: AsyncSession, user_id: int) -> int:
    """Number of connections the user owns."""
    stmt = (
        select(func.count())
        .select_from(Connection)
        .where(Connection.user_id == user_id, Connection.deleted_at.is_(None))
    )
    return (await db.scalar(stmt)) or 0


async def user_live_connection_count(db: AsyncSession, user_id: int) -> int:
    """Number of non-paused connections warm (seen within LIVE_WINDOW_SECONDS)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LIVE_WINDOW_SECONDS)
    stmt = (
        select(func.count())
        .select_from(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.deleted_at.is_(None),
            Connection.status != ConnectionStatus.PAUSED,
            Connection.last_seen_at >= cutoff,
        )
    )
    return (await db.scalar(stmt)) or 0


async def user_disconnected_connection_count(db: AsyncSession, user_id: int) -> int:
    """Number of non-paused connections that are not warm."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LIVE_WINDOW_SECONDS)
    stmt = (
        select(func.count())
        .select_from(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.deleted_at.is_(None),
            Connection.status != ConnectionStatus.PAUSED,
            (Connection.last_seen_at < cutoff) | Connection.last_seen_at.is_(None),
        )
    )
    return (await db.scalar(stmt)) or 0


async def user_has_agent(db: AsyncSession, user_id: int) -> bool:
    """True if the user owns at least one real (non-bot) AI agent."""
    stmt = (
        select(func.count())
        .select_from(Agent)
        .where(
            Agent.user_id == user_id,
            Agent.archived_at.is_(None),
            Agent.kind == AgentKind.AI,
        )
    )
    return bool(await db.scalar(stmt))


async def compute_nav_cta(db: AsyncSession, user: User | None) -> NavCta:
    """Resolve the Play CTA for this visitor.

    Agent-first ordering, matching how a player actually gets into a game:
    create an agent (the competitor) -> connect your AI -> play. A brand-new
    user is pointed at agent creation, NOT at the connector — you need a
    competitor before connecting one makes any sense.
    """
    if user is None:
        return NavCta(label="Get started", href="/play")
    if await user_has_connected_agent(db, user.id):
        return NavCta(label="Play now", href="/games/hoard-hurt-help#lobby-upcoming")
    if await user_has_agent(db, user.id):
        return NavCta(label="Connect your AI", href="/me/connections")
    return NavCta(label="Create your agent", href="/me/agents/new")


async def populate_nav_cta(request: Request, db: DbSession) -> None:
    """Router dependency: stash the Play CTA and connection count on ``request.state``.

    Skipped for HTMX fragment requests — those swap inner fragments that never
    contain the nav, so resolving the CTA (and its DB queries) would be wasted
    work on every poll.
    """
    if request.headers.get("HX-Request"):
        return
    user = await get_current_user(request, db)
    request.state.nav_cta = await compute_nav_cta(db, user)
    request.state.connection_count = (
        await user_connection_count(db, user.id) if user else 0
    )
    request.state.live_connection_count = (
        await user_live_connection_count(db, user.id) if user else 0
    )
    request.state.disconnected_connection_count = (
        await user_disconnected_connection_count(db, user.id) if user else 0
    )
