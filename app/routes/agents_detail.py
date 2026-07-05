"""The `/me/agents/{agent_id}` detail page — health, versions, and matches.

Builds the agent-detail template context (health badge, version ranking, match
list, join-gate state) and renders the onboarding-aware detail page.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from starlette.responses import Response

from app.config import PROVIDER_MODELS
from app.deps import DbSession, require_user_with_handle
from app.engine.agent_onboarding import compute_agent_onboarding_state
from app.engine.model_provider_match import provider_for_model
from app.engine.model_verification import model_status_for
from app.engine.connection_health import (
    ConnectionHealth,
    ConnectionHealthStatus,
    ProviderReadiness,
    active_matches_for_user,
    is_join_blocked,
    live_user_capacity,
    user_play_readiness,
)
from app.models.agent import Agent, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User
from app.read_models.matches import agent_has_active_match
from app.routes.agents_health_presenter import (
    MatchEntry,
    VersionRow,
    _count_agent_matches,
    _is_ready_to_play,
    health_view,
)
from app.routes.agents_queries import load_owned_agent
from app.templating import templates

router = APIRouter()


async def _load_agent_matches(db: DbSession, agent_id: int) -> list[MatchEntry]:
    """Return match rows for this agent: active first, then upcoming, then recent done (cap 10)."""
    rows = (
        await db.execute(
            select(Match, Player)
            .join(Player, Player.match_id == Match.id)
            .where(
                Player.agent_id == agent_id,
                Player.left_at.is_(None),
            )
            .order_by(Match.scheduled_start.desc())
        )
    ).all()

    active: list[MatchEntry] = []
    upcoming: list[MatchEntry] = []
    done: list[MatchEntry] = []

    for match, player in rows:
        pre_game = match.state in (GameState.SCHEDULED, GameState.REGISTERING)
        entry = MatchEntry(
            match_id=match.id,
            match_name=match.name,
            game_type=match.game,
            state=match.state,
            player_id=player.id,
            round_score=player.current_round_score,
            total_score=player.total_round_score,
            pre_game=pre_game,
        )
        if match.state == GameState.ACTIVE:
            active.append(entry)
        elif pre_game:
            upcoming.append(entry)
        else:
            done.append(entry)

    return active + upcoming + done[:10]


async def _version_rows(db: DbSession, agent_id: int) -> list[VersionRow]:
    rows = (
        await db.execute(
            select(
                AgentVersion,
                func.count(Player.id).label("match_count"),
                func.max(Match.completed_at).label("last_played_at"),
            )
            .join(Player, Player.agent_version_id == AgentVersion.id, isouter=True)
            .join(Match, Match.id == Player.match_id, isouter=True)
            .where(AgentVersion.agent_id == agent_id)
            .group_by(AgentVersion.id)
            .order_by(AgentVersion.version_no.desc(), AgentVersion.id.desc())
        )
    ).all()
    ranked = sorted(
        [
            (
                version,
                int(match_count or 0),
                last_played_at,
            )
            for version, match_count, last_played_at in rows
        ],
        key=lambda item: (-item[1], -item[0].version_no, item[0].created_at),
    )
    out: list[VersionRow] = []
    for index, (version, match_count, last_played_at) in enumerate(ranked, start=1):
        out.append(
            VersionRow(
                version=version,
                rank=index,
                match_count=match_count,
                last_played_at=last_played_at,
                frozen=version.frozen_at is not None,
            )
        )
    return sorted(out, key=lambda row: row.version.version_no)


async def _build_agent_detail_context(
    db: DbSession,
    request: Request,
    user: User,
    agent: Agent,
) -> dict[str, object]:
    """Build the template context for an agent detail / status page.

    Health and readiness are provider-agnostic — they reflect whether ANY of the
    user's live connections is up, since any connection can play any agent.
    """
    readiness = await user_play_readiness(db, user.id)

    # Build a health-like dict the templates can read (same keys as
    # ConnectionHealthStatus but not the dataclass itself). Map readiness rungs:
    #   PAUSED agent          → PAUSED state
    #   NO_MCP_CONNECTION     → DISCONNECTED / "No live connection" (needs connecting)
    #   CONNECTED_NOT_LIVE    → DISCONNECTED / "No live connection" (set up but offline)
    #   SEEN_NOT_POLLING/LIVE → READY (set up and recently seen or fully live)
    if agent.status == AgentStatus.PAUSED:
        status = ConnectionHealthStatus(
            state=ConnectionHealth.PAUSED,
            label="Paused",
            badge_class="badge-done",
            pulse=False,
            needs_reconnect=False,
            never_connected=False,
            last_connected_at=None,
            last_connected_human=None,
        )
    elif readiness in (ProviderReadiness.NO_MCP_CONNECTION, ProviderReadiness.CONNECTED_NOT_LIVE):
        status = ConnectionHealthStatus(
            state=ConnectionHealth.DISCONNECTED,
            label="No live connection",
            badge_class="badge-alert",
            pulse=False,
            needs_reconnect=True,
            never_connected=True,
            last_connected_at=None,
            last_connected_human=None,
        )
    else:
        # SEEN_NOT_POLLING or LIVE → ready to accept matches
        status = ConnectionHealthStatus(
            state=ConnectionHealth.READY,
            label="Ready",
            badge_class="badge-ok",
            pulse=False,
            needs_reconnect=False,
            never_connected=False,
            last_connected_at=None,
            last_connected_human=None,
        )
    health: object = health_view(status)

    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    versions = await _version_rows(db, agent.id)

    active_matches = await agent_has_active_match(db, agent.id)

    # SUM-based join-gate: the user's active matches vs. their total live capacity.
    active_match_count = await active_matches_for_user(db, user.id)
    capacity_sum = await live_user_capacity(db, user.id)
    join_blocked = is_join_blocked(active_match_count, capacity_sum)

    return {
        "user": user,
        "agent": agent,
        "version": version,
        "versions": versions,
        "health": health,
        "active_matches": active_matches,
        "active_match_count": active_match_count,
        "capacity_sum": capacity_sum,
        "join_blocked": join_blocked,
        "match_count": await _count_agent_matches(db, agent.id),
        # Advanced per-agent model picker (machine connections only; MCP ignores).
        "preferred_model": agent.preferred_model,
        "preferred_provider": provider_for_model(agent.preferred_model)
        if agent.preferred_model
        else None,
        "model_status": (
            (
                await model_status_for(
                    db,
                    user.id,
                    provider_for_model(agent.preferred_model) or "",
                    agent.preferred_model,
                )
            ).value
            if agent.preferred_model and provider_for_model(agent.preferred_model)
            else None
        ),
        "model_options": [
            (provider, models)
            for provider, models in PROVIDER_MODELS.items()
            if models
        ],
    }


@router.get("/{agent_id}", response_class=HTMLResponse)
async def agent_detail(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agent = await load_owned_agent(db, user, agent_id)
    context = await _build_agent_detail_context(db, request, user, agent)
    matches = await _load_agent_matches(db, agent.id)
    # Under coverage-based routing, "connected" means the provider is currently
    # covered by a live connection.  We pass a non-None sentinel (True) when
    # covered so compute_agent_onboarding_state advances past state-1 (waiting).
    health = context.get("health")
    _health_state = (
        health.get("state") if isinstance(health, dict) else getattr(health, "state", None)
    )
    first_connected_at: object = (
        True
        if _health_state in (ConnectionHealth.READY, ConnectionHealth.LIVE)
        else None
    )
    onboarding = await compute_agent_onboarding_state(
        db,
        agent_id=agent.id,
        first_connected_at=first_connected_at,
        matches=list(matches),
    )
    context = {
        **context,
        "matches": matches,
        "onboarding": onboarding,
        "ready_to_play": _is_ready_to_play(context),
    }
    return templates.TemplateResponse(request, "agents/detail.html", context)
