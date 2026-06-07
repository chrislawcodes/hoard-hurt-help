"""Connection list, creation, and detail routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.responses import Response

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import compute_connection_health
from app.engine.pending_connection_gc import gc_pending_connections
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.user import User
from app.templating import templates

router = APIRouter()


_PROVIDER_LABELS = {
    ConnectionProvider.CLAUDE.value: "Claude",
    ConnectionProvider.GEMINI.value: "Gemini",
    ConnectionProvider.OPENAI.value: "OpenAI",
    ConnectionProvider.HERMES.value: "Hermes",
    ConnectionProvider.OPENCLAW.value: "OpenClaw",
}


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None


def _provider_label(provider: ConnectionProvider) -> str:
    return _PROVIDER_LABELS.get(provider.value, provider.value.title())


def _connection_display_name(connection: Connection) -> str:
    return connection.nickname or _provider_label(connection.provider)


def _runner_setup_message(connection: Connection, key: str) -> str:
    provider_label = _provider_label(connection.provider)
    return (
        "Please set up my AI connection in the background and keep it running.\n\n"
        "Run these commands:\n"
        f"curl -fsSL {settings.base_url}/runners/agentludum_connector.py -o agentludum_connector.py\n"
        f"python3 agentludum_connector.py --key {key} --url {settings.base_url}\n\n"
        f"This connection uses the {provider_label} login I already have. "
        "Use the X-Connection-Key header with that key when the runner talks to the server.\n\n"
        "Keep this running so it plays all my agents' games. Keep one session per match, "
        "and only think when it is that agent's turn.\n\n"
        "If the server says the key is invalid, stop and tell me. I can reissue it from the connection page."
    )


async def _load_attached_agents(db: DbSession, connection_id: int) -> list[AgentRow]:
    rows = (
        (
            await db.execute(
                select(Agent, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
                .where(
                    Agent.connection_id == connection_id,
                    Agent.kind == AgentKind.AI,
                    Agent.archived_at.is_(None),
                )
                .order_by(Agent.name)
            )
        )
        .all()
    )
    return [AgentRow(agent=agent, version=version) for agent, version in rows]


async def _load_detached_agents(
    db: DbSession, user_id: int, provider: ConnectionProvider
) -> list[AgentRow]:
    rows = (
        (
            await db.execute(
                select(Agent, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
                .where(
                    Agent.user_id == user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.connection_id.is_(None),
                    Agent.status == AgentStatus.PAUSED,
                    Agent.archived_at.is_(None),
                )
                .order_by(Agent.name)
            )
        )
        .all()
    )
    allowed = set(PROVIDER_MODELS.get(provider.value, ()))
    rows_out: list[AgentRow] = []
    for agent, version in rows:
        if version is None or version.model not in allowed:
            continue
        rows_out.append(AgentRow(agent=agent, version=version))
    return rows_out


async def _load_owned_connection(db: DbSession, user: User, connection_id: int) -> Connection:
    connection = (
        await db.execute(
            select(Connection).where(
                Connection.id == connection_id,
                Connection.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found.")
    return connection


async def _load_resumeable_pending_connection(
    db: DbSession, user_id: int, provider: ConnectionProvider
) -> Connection | None:
    """Return the newest pending connection for this provider, if one exists."""
    return (
        await db.execute(
            select(Connection)
            .where(
                Connection.user_id == user_id,
                Connection.provider == provider,
                Connection.status == ConnectionStatus.PENDING,
                Connection.first_connected_at.is_(None),
            )
            .order_by(Connection.created_at.desc(), Connection.id.desc())
        )
    ).scalar_one_or_none()


def _issue_connection_key(connection: Connection, *, keep_old_overlap: bool) -> str:
    key = generate_connection_key()
    if keep_old_overlap and connection.prev_key_lookup is None:
        connection.prev_key_lookup = connection.key_lookup
    connection.key_lookup = bot_key_lookup(key)
    connection.key_hint = bot_key_hint(key)
    if not keep_old_overlap:
        connection.prev_key_lookup = None
    return key


@router.get("", response_class=HTMLResponse)
async def list_connections(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    await gc_pending_connections(db)
    connections = (
        (
            await db.execute(
                select(Connection)
                .where(Connection.user_id == user.id)
                .order_by(Connection.created_at.desc(), Connection.id.desc())
            )
        )
        .scalars()
        .all()
    )
    rows = []
    for connection in connections:
        health = await compute_connection_health(db, connection)
        rows.append(
            {
                "connection": connection,
                "display_name": _connection_display_name(connection),
                "health": health,
                "agents": await _load_attached_agents(db, connection.id),
            }
        )
    return templates.TemplateResponse(
        request,
        "connections/list.html",
        {
            "user": user,
            "connections": rows,
            "provider_choices": list(ConnectionProvider),
            "provider_labels": _PROVIDER_LABELS,
        },
    )


@router.post("")
async def create_connection(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    provider: Annotated[str, Form()],
    nickname: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    try:
        provider_choice = ConnectionProvider(provider.strip().lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown provider.") from exc

    key = generate_connection_key()
    connection = await _load_resumeable_pending_connection(db, user.id, provider_choice)
    if connection is None:
        connection = Connection(
            user_id=user.id,
            nickname=(nickname.strip() if nickname and nickname.strip() else None),
            provider=provider_choice,
            key_lookup=bot_key_lookup(key),
            key_hint=bot_key_hint(key),
            status=ConnectionStatus.PENDING,
        )
        db.add(connection)
        await db.flush()
    elif nickname and nickname.strip():
        connection.nickname = nickname.strip()
        key = _issue_connection_key(connection, keep_old_overlap=True)
    await db.commit()
    request.session[f"fresh_connection_key_{connection.id}"] = key
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{connection_id}", response_class=HTMLResponse)
async def connection_detail(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    connection = await _load_owned_connection(db, user, connection_id)
    fresh_key = request.session.pop(f"fresh_connection_key_{connection.id}", None)
    health = await compute_connection_health(db, connection)
    attached_agents = await _load_attached_agents(db, connection.id)
    detached_agents = await _load_detached_agents(db, user.id, connection.provider)
    return templates.TemplateResponse(
        request,
        "connections/detail.html",
        {
            "user": user,
            "connection": connection,
            "display_name": _connection_display_name(connection),
            "health": health,
            "fresh_key": fresh_key,
            "runner_message": (
                _runner_setup_message(connection, fresh_key) if fresh_key else None
            ),
            "attached_agents": attached_agents,
            "detached_agents": detached_agents,
            "provider_label": _provider_label(connection.provider),
            "provider_models": PROVIDER_MODELS.get(connection.provider.value, []),
            "base_url": settings.base_url,
            "agent_count": len(attached_agents),
        },
    )


@router.get("/{connection_id}/status", response_class=HTMLResponse)
async def connection_status_fragment(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    connection = await _load_owned_connection(db, user, connection_id)
    return templates.TemplateResponse(
        request,
        "connections/_status.html",
        {
            "connection": connection,
            "display_name": _connection_display_name(connection),
            "health": await compute_connection_health(db, connection),
            "agent_count": len(await _load_attached_agents(db, connection.id)),
        },
    )


@router.get("/{connection_id}/health-badge", response_class=HTMLResponse)
async def connection_health_badge_fragment(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    connection = await _load_owned_connection(db, user, connection_id)
    return templates.TemplateResponse(
        request,
        "connections/_health_badge.html",
        {
            "connection": connection,
            "health": await compute_connection_health(db, connection),
        },
    )
