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

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSession, get_current_user
from app.engine.connection_health import (
    LIVE_WINDOW_SECONDS,
    ProviderReadiness,
    enabled_provider_values,
    provider_readiness,
)
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.match import Match
from app.models.user import User
from app.routes.web_support import safe_internal_next


# The games lobby anchor every "ready to play" path lands on (nav CTA, /play,
# and the play-setup resolver's READY/NEEDS_LIVE case).
LOBBY_URL = "/games/hoard-hurt-help#lobby-upcoming"


@dataclass(frozen=True)
class NavCta:
    """The single Play button: a label and where it points."""

    label: str
    href: str


class PlaySetupStage(enum.IntEnum):
    """The gate ladder a visitor climbs to actually play, lowest→highest.

    Each rung is the *first unmet gate* on the path to playing. The integer
    order is load-bearing: the resolver compares the first-unmet gate against the
    caller's ``require`` bar to decide whether everything the caller cares about
    is already satisfied (``READY``).
    """

    NOT_SIGNED_IN = 0
    NEEDS_HANDLE = 1
    NEEDS_AGENT = 2
    NEEDS_MCP_CONNECTION = 3
    NEEDS_LIVE = 4
    READY = 5


@dataclass(frozen=True)
class PlaySetupState:
    """The resolved play-setup gate plus where to send the visitor next."""

    stage: PlaySetupStage
    next_url: str


# Map a provider's ProviderReadiness to the *first unmet* play-setup gate.
# LIVE has no unmet gate (fully ready) and so is absent from this map.
_READINESS_TO_FIRST_UNMET: dict[ProviderReadiness, PlaySetupStage] = {
    ProviderReadiness.NO_MCP_CONNECTION: PlaySetupStage.NEEDS_MCP_CONNECTION,
    ProviderReadiness.CONNECTED_NOT_LIVE: PlaySetupStage.NEEDS_LIVE,
    ProviderReadiness.SEEN_NOT_POLLING: PlaySetupStage.NEEDS_LIVE,
}


async def user_connection_count(db: AsyncSession, user_id: int) -> int:
    """Number of connections the user owns."""
    stmt = (
        select(func.count())
        .select_from(Connection)
        .where(Connection.user_id == user_id, Connection.deleted_at.is_(None))
    )
    return (await db.scalar(stmt)) or 0


async def user_live_connection_count(db: AsyncSession, user_id: int) -> int:
    """Distinct providers with a live connection (seen within LIVE_WINDOW_SECONDS)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LIVE_WINDOW_SECONDS)
    stmt = (
        select(func.count(Connection.provider.distinct()))
        .select_from(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.deleted_at.is_(None),
            Connection.status != ConnectionStatus.PAUSED,
            Connection.provider.is_not(None),
            Connection.last_seen_at >= cutoff,
        )
    )
    return (await db.scalar(stmt)) or 0


async def user_has_any_ai_agent(db: AsyncSession, user_id: int) -> bool:
    """True when the user has at least one non-archived AI agent (seatable).

    Agents are no longer tied to a provider, so existence is all that matters —
    whichever AI client the user connects can play any of them.
    """
    row = (
        await db.execute(
            select(Agent.id)
            .where(
                Agent.user_id == user_id,
                Agent.archived_at.is_(None),
                Agent.kind == AgentKind.AI,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _user_connection_providers(
    db: AsyncSession, user_id: int
) -> list[ConnectionProvider]:
    """Distinct providers the user has set up on any connection.

    Connection readiness is provider-agnostic for play (any live connection can
    serve any agent), but readiness itself is still computed per provider, so we
    reduce over the providers the user actually has connected.
    """
    values = await enabled_provider_values(db, user_id)
    return [ConnectionProvider(v) for v in values]


def _setup_gate_url(
    stage: PlaySetupStage,
    *,
    provider: ConnectionProvider | None,
    target_match: Match | None,
) -> str:
    """The destination for a given stage. The resolver owns this mapping."""
    match_url = (
        f"/games/{target_match.game}/matches/{target_match.id}"
        if target_match is not None
        else None
    )
    lobby_url = LOBBY_URL

    if stage == PlaySetupStage.NOT_SIGNED_IN:
        return "/auth/google/login"
    if stage == PlaySetupStage.NEEDS_HANDLE:
        base = "/me/handle"
        return _with_join_next(base, target_match)
    if stage == PlaySetupStage.NEEDS_AGENT:
        base = "/me/agents/new"
        return _with_join_next(base, target_match)
    if stage == PlaySetupStage.NEEDS_MCP_CONNECTION:
        suffix = f"?provider={provider.value}" if provider is not None else ""
        base = f"/me/connections{suffix}"
        return _with_join_next(base, target_match)
    # NEEDS_LIVE and READY both land on the match (if any) or the lobby.
    return match_url or lobby_url


def _with_join_next(base: str, target_match: Match | None) -> str:
    """Append a safe ``next=`` back to the match's join URL when joining.

    Only setup gates call this. When a match is the join target, finishing the
    gate should bounce back to its join, so we thread the join URL through
    ``safe_internal_next`` and append it with the correct separator.
    """
    if target_match is None:
        return base
    join_url = safe_internal_next(
        f"/games/{target_match.game}/matches/{target_match.id}/join"
    )
    if join_url is None:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}next={join_url}"


async def resolve_play_setup_state(
    db: AsyncSession,
    user: User | None,
    *,
    target_match: Match | None = None,
    target_agent: Agent | None = None,
    require: PlaySetupStage = PlaySetupStage.NEEDS_MCP_CONNECTION,
) -> PlaySetupState:
    """Resolve the visitor's first unmet play-setup gate, clamped by ``require``.

    Computes the first gate the visitor has not yet cleared on the path to
    playing, then clamps: if that first-unmet gate is *above* the caller's
    ``require`` bar (everything the caller cares about is already satisfied), the
    result is ``READY``. Otherwise it is the first-unmet gate itself.

    ``require`` is the minimum stage the caller treats as "done." Nav uses
    ``NEEDS_MCP_CONNECTION`` (a set-up agent shows "Play now" even if its loop
    isn't running); join-confirm uses ``READY`` (the seat waits for ``LIVE``).

    With no ``target_agent`` (global intent) the readiness check reduces over the
    user's seatable AI agents to the most-ready provider (AD-4), with a
    dedup-then-early-exit cost bound (see ``_reduce_most_ready``).
    """
    first_unmet, provider = await _first_unmet_gate(
        db, user, target_agent=target_agent, require=require
    )
    stage = (
        PlaySetupStage.READY
        if first_unmet is None or first_unmet > require
        else first_unmet
    )
    next_url = _setup_gate_url(stage, provider=provider, target_match=target_match)
    return PlaySetupState(stage=stage, next_url=next_url)


async def _first_unmet_gate(
    db: AsyncSession,
    user: User | None,
    *,
    target_agent: Agent | None,
    require: PlaySetupStage,
) -> tuple[PlaySetupStage | None, ConnectionProvider | None]:
    """Return (first-unmet gate, provider). ``None`` gate ⇒ fully ready.

    Provider is always ``None`` now: agents aren't tied to one and the connect
    page is no longer provider-scoped. The slot is kept so callers/signatures
    don't churn.
    """
    if user is None:
        return PlaySetupStage.NOT_SIGNED_IN, None
    if user.handle is None:
        return PlaySetupStage.NEEDS_HANDLE, None

    # Need at least one agent to play. A target agent proves one exists.
    if target_agent is None and not await user_has_any_ai_agent(db, user.id):
        return PlaySetupStage.NEEDS_AGENT, None

    # Connection readiness is provider-agnostic: any of the user's live
    # connections can serve any agent. Reduce readiness over the providers the
    # user actually has connected; no connection at all ⇒ NEEDS_MCP_CONNECTION.
    providers = await _user_connection_providers(db, user.id)
    if not providers:
        return PlaySetupStage.NEEDS_MCP_CONNECTION, None
    _provider, readiness = await _reduce_most_ready(db, user.id, providers, require=require)
    return _READINESS_TO_FIRST_UNMET.get(readiness), None


async def _reduce_most_ready(
    db: AsyncSession,
    user_id: int,
    providers: list[ConnectionProvider],
    *,
    require: PlaySetupStage,
) -> tuple[ConnectionProvider, ProviderReadiness]:
    """Most-ready reduction with early-exit on the ``require`` bar (AD-4).

    Early-exit: the first provider whose first-unmet gate is *above* ``require``
    (already clears the caller's bar) ends the loop. For nav that is the first
    provider clearing ``provider_has_current_setup`` — so a single-provider ready
    user costs ~1 readiness call, not 3·K. When no provider clears the bar, the
    most-ready provider is returned so the resolver still reports the nearest gate.
    """
    best_provider = providers[0]
    best_readiness = ProviderReadiness.NO_MCP_CONNECTION
    rank = {
        ProviderReadiness.NO_MCP_CONNECTION: 0,
        ProviderReadiness.CONNECTED_NOT_LIVE: 1,
        ProviderReadiness.SEEN_NOT_POLLING: 2,
        ProviderReadiness.LIVE: 3,
    }
    best_rank = -1
    for provider in providers:
        readiness = await provider_readiness(db, user_id, provider)
        first_unmet = _READINESS_TO_FIRST_UNMET.get(readiness)
        if first_unmet is None or first_unmet > require:
            return provider, readiness
        if rank[readiness] > best_rank:
            best_rank = rank[readiness]
            best_provider, best_readiness = provider, readiness
    return best_provider, best_readiness


async def compute_nav_cta(db: AsyncSession, user: User | None) -> NavCta:
    """Resolve the Play CTA for this visitor.

    Signed out: "Get started" → ``/play`` (which routes to sign-in). Signed in:
    always "Play now" → the lobby. The nav stays dumb on purpose — all setup
    gating (handle, agent, MCP connection, live) happens on the join flow, not
    on this button.
    """
    if user is None:
        return NavCta(label="Get started", href="/play")
    return NavCta(label="Play now", href=LOBBY_URL)


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
