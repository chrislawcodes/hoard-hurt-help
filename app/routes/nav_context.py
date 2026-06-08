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

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSession, get_current_user
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection
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
    """
    stmt = (
        select(func.count())
        .select_from(Agent)
        .join(Connection, Connection.id == Agent.connection_id)
        .where(
            Agent.user_id == user_id,
            Agent.archived_at.is_(None),
            Agent.kind == AgentKind.AI,
            Connection.first_connected_at.is_not(None),
        )
    )
    return bool(await db.scalar(stmt))


async def user_connection_count(db: AsyncSession, user_id: int) -> int:
    """Number of connections the user owns."""
    stmt = (
        select(func.count())
        .select_from(Connection)
        .where(Connection.user_id == user_id)
    )
    return (await db.scalar(stmt)) or 0


async def compute_nav_cta(db: AsyncSession, user: User | None) -> NavCta:
    """Resolve the Play CTA for this visitor."""
    if user is None:
        return NavCta(label="Get started", href="/play")
    if await user_has_connected_agent(db, user.id):
        return NavCta(label="Play now", href="/play")
    if await user_connection_count(db, user.id) > 0:
        return NavCta(label="Create an Agent", href="/me/agents/new")
    return NavCta(label="Connect your AI", href="/me/connections")


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
