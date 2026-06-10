"""Agent list, creation, and detail routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from starlette.responses import Response

from app.config import PROVIDER_MODELS, provider_for_model
from app.deps import DbSession, require_user_with_handle
from app.engine.agent_onboarding import compute_agent_onboarding_state
from app.engine.connection_health import ConnectionHealth, compute_connection_health
from app.engine.pending_connection_gc import gc_pending_connections
from app.games import get as get_game_module, known_types
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User
from app.routes.connections_setup import (
    _provider_label,
)
from app.templating import templates

router = APIRouter()

_DEFAULT_GAME = known_types()[0] if known_types() else "hoard-hurt-help"


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None
    connection: Connection | None
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


async def _load_owned_connection(
    db: DbSession, user: User, connection_id: int
) -> Connection:
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


async def _load_active_connection_for_provider(
    db: DbSession, user_id: int, provider: ConnectionProvider
) -> Connection | None:
    rows = (
        await db.execute(
            select(Connection)
            .where(
                Connection.user_id == user_id,
                Connection.provider == provider,
                Connection.status == ConnectionStatus.ACTIVE,
                Connection.deleted_at.is_(None),
            )
            .order_by(Connection.created_at.desc(), Connection.id.desc())
        )
    ).scalars()
    return rows.first()


async def _load_user_connections(db: DbSession, user_id: int) -> list[Connection]:
    rows = (
        await db.execute(
            select(Connection)
            .where(Connection.user_id == user_id, Connection.deleted_at.is_(None))
            .order_by(Connection.created_at.desc(), Connection.id.desc())
        )
    )
    return list(rows.scalars().all())


async def _enabled_provider_values(db: DbSession, user_id: int) -> set[str]:
    """Provider values enabled on at least one of the user's live-or-not
    connections — the providers an agent can be created for."""
    rows = (
        (
            await db.execute(
                select(ConnectionProviderRow.provider)
                .join(Connection, Connection.id == ConnectionProviderRow.connection_id)
                .where(
                    Connection.user_id == user_id,
                    Connection.deleted_at.is_(None),
                    ConnectionProviderRow.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return {p.value for p in rows}


def _build_model_picker_groups(
    enabled_provider_values: set[str], selected_model: str | None
) -> tuple[list[dict[str, object]], str | None, list[str]]:
    """Return grouped model options plus the first selectable model and disabled notes."""
    groups: list[dict[str, object]] = []
    notes: list[str] = []
    first_enabled: str | None = None
    first_any: str | None = None
    for provider_value, models in PROVIDER_MODELS.items():
        provider = ConnectionProvider(provider_value)
        enabled = provider_value in enabled_provider_values
        options: list[dict[str, str]] = [{"value": model, "label": model} for model in models]
        if options and first_any is None:
            first_any = options[0]["value"]
        if enabled and options and first_enabled is None:
            first_enabled = options[0]["value"]
        if not enabled:
            notes.append(
                f"No machine runs {_provider_label(provider)} — turn it on at /me/connections."
            )
        groups.append(
            {
                "provider_value": provider_value,
                "provider_label": _provider_label(provider),
                "enabled": enabled,
                "options": options,
            }
        )
    selected = selected_model
    if selected is None:
        selected = first_enabled or first_any
    return groups, selected, notes


async def _load_user_agents(db: DbSession, user_id: int) -> list[tuple[Agent, AgentVersion | None, Connection | None]]:
    rows = (
        await db.execute(
            select(Agent, AgentVersion, Connection)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .join(Connection, Connection.id == Agent.connection_id, isouter=True)
            .where(
                Agent.user_id == user_id,
                Agent.kind == AgentKind.AI,
                Agent.archived_at.is_(None),
            )
            .order_by(Agent.created_at.desc(), Agent.id.desc())
        )
    ).all()
    return [(agent, version, connection) for agent, version, connection in rows]


async def _count_agent_matches(db: DbSession, agent_id: int) -> int:
    count = await db.scalar(
        select(func.count()).select_from(Player).where(Player.agent_id == agent_id)
    )
    return int(count or 0)


async def _count_active_matches_for_connection(db: DbSession, connection_id: int) -> int:
    count = await db.scalar(
        select(func.count(func.distinct(Match.id)))
        .select_from(Agent)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .where(
            Agent.connection_id == connection_id,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state == GameState.ACTIVE,
        )
    )
    return int(count or 0)


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
    connection: Connection | None = None
    if agent.connection_id is not None:
        connection = (
            await db.execute(
                select(Connection).where(
                    Connection.id == agent.connection_id,
                    Connection.user_id == user.id,
                    Connection.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    health: object = None
    if connection is not None:
        health = await compute_connection_health(db, connection)
    elif agent.status == AgentStatus.PAUSED:
        health = {
            "state": ConnectionHealth.PAUSED,
            "label": "Needs connection",
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
        health = {
            "state": ConnectionHealth.DISCONNECTED,
            "label": "Disconnected",
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
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    versions = await _version_rows(db, agent.id)
    allowed_models = (
        PROVIDER_MODELS.get(connection.provider.value, [])
        if connection is not None and connection.provider is not None
        else []
    )
    candidate_connections: list[Connection] = []
    if agent.connection_id is not None and connection is not None and connection.provider is not None:
        candidate_connections = [
            item
            for item in await _load_user_connections(db, user.id)
            if item.provider == connection.provider
            and item.status != ConnectionStatus.PENDING
        ]
    elif version is not None:
        candidate_connections = [
            item
            for item in await _load_user_connections(db, user.id)
            if item.provider is not None
            and version.model in PROVIDER_MODELS.get(item.provider.value, [])
        ]
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
    active_match_count = (
        await _count_active_matches_for_connection(db, connection.id)
        if connection is not None
        else 0
    )
    return {
        "user": user,
        "agent": agent,
        "connection": connection,
        "version": version,
        "versions": versions,
        "health": health,
        "provider_label": _provider_label(connection.provider) if connection else None,
        "provider_models": allowed_models,
        "candidate_connections": candidate_connections,
        "active_matches": active_matches,
        "active_match_count": active_match_count,
        "join_blocked": connection is not None and active_match_count >= connection.max_concurrent_games,
        "match_count": await _count_agent_matches(db, agent.id),
    }


@router.get("", response_class=HTMLResponse)
async def list_agents(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agents = await _load_user_agents(db, user.id)
    rows: list[AgentRow] = []
    for agent, version, connection in agents:
        health = (
            await compute_connection_health(db, connection)
            if connection is not None
            else {
                "state": ConnectionHealth.PAUSED,
                "label": "Needs connection",
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
        )
        rows.append(
            AgentRow(
                agent=agent,
                version=version,
                connection=connection,
                health=health,
                match_count=await _count_agent_matches(db, agent.id),
            )
        )
    return templates.TemplateResponse(
        request,
        "agents/list.html",
        {
            "user": user,
            "agents": rows,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_agent_form(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    provider: str | None = None,
) -> Response:
    await gc_pending_connections(db)
    connections = await _load_user_connections(db, user.id)
    enabled_provider_values = await _enabled_provider_values(db, user.id)
    requested_provider = provider.strip().lower() if provider and provider.strip() else None
    selected_model = None
    if requested_provider is not None:
        for provider_value, models in PROVIDER_MODELS.items():
            if provider_value == requested_provider and models:
                selected_model = models[0]
                break
    model_groups, selected_model, availability_notes = _build_model_picker_groups(
        enabled_provider_values, selected_model
    )
    strategy_presets = [
        {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "prompt": preset.prompt,
        }
        for preset in get_game_module(_DEFAULT_GAME).strategy_presets()
    ]
    context: dict[str, object] = {
        "user": user,
        "connections": connections,
        "model_groups": model_groups,
        "selected_model": selected_model,
        "availability_notes": availability_notes,
        "default_game": _DEFAULT_GAME,
        "default_strategy": get_game_module(_DEFAULT_GAME).default_strategy(),
        "strategy_presets": strategy_presets,
        "selected_strategy_preset": strategy_presets[0]["id"] if strategy_presets else "",
        "selected_strategy_text": (
            strategy_presets[0]["prompt"]
            if strategy_presets
            else get_game_module(_DEFAULT_GAME).default_strategy()
        ),
    }
    return templates.TemplateResponse(request, "agents/new.html", context)


@router.post("/new")
async def create_agent_or_connection(
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    name: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    strategy_text: Annotated[str | None, Form()] = None,
    strategy_preset: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Agent name is required.")
        existing = (
            await db.execute(
                select(Agent).where(
                    Agent.user_id == user.id,
                    Agent.name == clean_name,
                    Agent.archived_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="You already have an agent with that name.")

        clean_model = (model or "").strip()
        if not clean_model:
            raise HTTPException(status_code=400, detail="Model is required.")
        derived = provider_for_model(clean_model)
        if derived is None:
            raise HTTPException(status_code=400, detail="Unknown model.")
        agent_provider = ConnectionProvider(derived)
        if agent_provider.value not in await _enabled_provider_values(db, user.id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No machine runs {_provider_label(agent_provider)}. "
                    "Turn it on at /me/connections first."
                ),
            )
        clean_strategy = (strategy_text or "").strip()
        if not clean_strategy and strategy_preset:
            preset = next(
                (
                    item
                    for item in get_game_module(_DEFAULT_GAME).strategy_presets()
                    if item.id == strategy_preset
                ),
                None,
            )
            clean_strategy = preset.prompt if preset is not None else ""
        version_text = clean_strategy or get_game_module(_DEFAULT_GAME).default_strategy()
        agent = Agent(
            user_id=user.id,
            connection_id=None,
            provider=agent_provider,
            kind=AgentKind.AI,
            name=clean_name,
            game=_DEFAULT_GAME,
            status=AgentStatus.ACTIVE,
        )
        db.add(agent)
        await db.flush()
        version = AgentVersion(
            agent_id=agent.id,
            version_no=1,
            model=clean_model,
            strategy_text=version_text,
        )
        db.add(version)
        await db.flush()
        agent.current_version_id = version.id
        await db.commit()
        return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)

    raise HTTPException(status_code=400, detail="Agent name is required.")


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
    raw_connection = context.get("connection")
    connection_obj = raw_connection if isinstance(raw_connection, Connection) else None
    # Use first_connected_at with a fallback to last_seen_at for legacy connections
    # created before first_connected_at was tracked.
    first_connected_at = (
        (connection_obj.first_connected_at or connection_obj.last_seen_at)
        if connection_obj is not None
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
