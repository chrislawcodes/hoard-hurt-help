"""The `/me/agents/{agent_id}` detail page — health, versions, and matches.

Builds the agent-detail template context (health badge, version ranking, match
list, join-gate state) and renders the onboarding-aware detail page.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from starlette.responses import Response

from app.deps import DbSession, require_user_with_handle
from app.engine.agent_onboarding import compute_agent_onboarding_state
from app.engine.connection_health import (
    ConnectionHealth,
    ProviderReadiness,
    active_matches_for_user,
    is_join_blocked,
    live_user_capacity,
    user_play_readiness,
)
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User
from app.routes.agents_health_presenter import (
    MatchEntry,
    VersionRow,
    _count_agent_matches,
    _is_ready_to_play,
)
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
        health: object = {
            "state": ConnectionHealth.PAUSED,
            "label": "Paused",
            "badge_class": "badge-done",
            "pulse": False,
            "needs_reconnect": False,
            "never_connected": False,
            "last_connected_at": None,
            "last_connected_human": None,
            "match_id": None,
            "game_name": None,
            "agent_count": 0,
        }
    elif readiness in (ProviderReadiness.NO_MCP_CONNECTION, ProviderReadiness.CONNECTED_NOT_LIVE):
        health = {
            "state": ConnectionHealth.DISCONNECTED,
            "label": "No live connection",
            "badge_class": "badge-alert",
            "pulse": False,
            "needs_reconnect": True,
            "never_connected": True,
            "last_connected_at": None,
            "last_connected_human": None,
            "match_id": None,
            "game_name": None,
            "agent_count": 0,
        }
    else:
        # SEEN_NOT_POLLING or LIVE → ready to accept matches
        health = {
            "state": ConnectionHealth.READY,
            "label": "Ready",
            "badge_class": "badge-ok",
            "pulse": False,
            "needs_reconnect": False,
            "never_connected": False,
            "last_connected_at": None,
            "last_connected_human": None,
            "match_id": None,
            "game_name": None,
            "agent_count": 0,
        }

    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    versions = await _version_rows(db, agent.id)

    active_matches = (
        await db.execute(
            select(Match.id)
            .join(Player, Player.match_id == Match.id)
            .where(
                Player.agent_id == agent.id,
                Player.left_at.is_(None),
                Match.state == GameState.ACTIVE,
            )
            .limit(1)
        )
    ).first() is not None

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
    }


@router.get("/{agent_id}", response_class=HTMLResponse)
async def agent_detail(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
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
