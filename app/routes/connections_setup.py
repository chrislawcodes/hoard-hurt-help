"""Connection list, creation, and detail routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from starlette.responses import Response

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import compute_connection_health
from app.engine.pending_connection_gc import gc_pending_connections
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_setup import ConnectionSetup
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

_PROVIDER_GROUPS = [
    {
        "label": "Claude / Gemini / OpenAI",
        "description": "Use the standard setup path for the CLI-backed providers.",
        "providers": [
            ConnectionProvider.CLAUDE,
            ConnectionProvider.GEMINI,
            ConnectionProvider.OPENAI,
        ],
    },
    {
        "label": "Hermes / OpenClaw",
        "description": "Use the Hermes/OpenClaw setup path.",
        "providers": [
            ConnectionProvider.HERMES,
            ConnectionProvider.OPENCLAW,
        ],
    },
]


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None


def _provider_label(provider: ConnectionProvider) -> str:
    return _PROVIDER_LABELS.get(provider.value, provider.value.title())


def _connection_display_name(connection: Connection) -> str:
    return connection.nickname or _provider_label(connection.provider)


def _setup_message(provider_label: str, key: str) -> str:
    return (
        "Please set up my AI connection as a persistent background service that starts "
        "automatically on login and restarts if it ever stops.\n\n"
        "Step 1 — save the setup script to a permanent location:\n"
        "  macOS/Linux: mkdir -p ~/.agentludum && "
        f"curl -fsSL {settings.base_url}/setup-files/agentludum_connector.py"
        " -o ~/.agentludum/agentludum_connector.py\n"
        "  Windows:     mkdir %USERPROFILE%\\.agentludum && "
        f"curl -fsSL {settings.base_url}/setup-files/agentludum_connector.py"
        " -o %USERPROFILE%\\.agentludum\\agentludum_connector.py\n\n"
        "Step 2 — install a system service so it runs on login and auto-restarts:\n"
        "  macOS:   launchd plist in ~/Library/LaunchAgents/ with KeepAlive=true and RunAtLoad=true\n"
        "  Linux:   systemd user unit (~/.config/systemd/user/) with Restart=on-failure, "
        "then systemctl --user enable --now\n"
        "  Windows: Task Scheduler task triggered on login with restart-on-failure enabled\n\n"
        "Setup command:\n"
        f"  macOS/Linux: python3 ~/.agentludum/agentludum_connector.py --key {key} --url {settings.base_url}\n"
        f"  Windows:     python %USERPROFILE%\\.agentludum\\agentludum_connector.py --key {key} --url {settings.base_url}\n\n"
        f"This connection uses the {provider_label} login I already have. "
        "Use the X-Connection-Key header with that key when the client talks to the server.\n\n"
        "Keep one session per match and only think when it is that agent's turn.\n\n"
        "If the server says the key is invalid, stop the service and tell me. "
        "I can rotate it from the connection page."
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
                Connection.deleted_at.is_(None),
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
                Connection.deleted_at.is_(None),
                Connection.first_connected_at.is_(None),
            )
            .order_by(Connection.created_at.desc(), Connection.id.desc())
        )
    ).scalar_one_or_none()


async def _load_pending_setups(db: DbSession, user_id: int) -> list[ConnectionSetup]:
    try:
        rows = (
            (
                await db.execute(
                    select(ConnectionSetup)
                    .where(
                        ConnectionSetup.user_id == user_id,
                        ConnectionSetup.completed_at.is_(None),
                    )
                    .order_by(ConnectionSetup.created_at.desc(), ConnectionSetup.id.desc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
    except OperationalError:
        return []


async def _load_resumeable_pending_setup(
    db: DbSession, user_id: int, provider: ConnectionProvider
) -> ConnectionSetup | None:
    """Return the newest pending setup for this provider, if one exists."""
    return (
        await db.execute(
            select(ConnectionSetup)
            .where(
                ConnectionSetup.user_id == user_id,
                ConnectionSetup.provider == provider,
                ConnectionSetup.completed_at.is_(None),
            )
            .order_by(ConnectionSetup.created_at.desc(), ConnectionSetup.id.desc())
        )
    ).scalar_one_or_none()


def _issue_setup_key(setup: ConnectionSetup) -> str:
    key = generate_connection_key()
    setup.key_lookup = bot_key_lookup(key)
    setup.key_hint = bot_key_hint(key)
    return key


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
                .where(Connection.user_id == user.id, Connection.deleted_at.is_(None))
                .order_by(Connection.created_at.desc(), Connection.id.desc())
            )
        )
        .scalars()
        .all()
    )
    pending_setups = await _load_pending_setups(db, user.id)
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
            "pending_setups": pending_setups,
            "provider_choices": list(ConnectionProvider),
            "provider_labels": _PROVIDER_LABELS,
            "provider_groups": _PROVIDER_GROUPS,
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

    try:
        setup = await _load_resumeable_pending_setup(db, user.id, provider_choice)
        if setup is None:
            key = generate_connection_key()
            setup = ConnectionSetup(
                user_id=user.id,
                nickname=(nickname.strip() if nickname and nickname.strip() else None),
                provider=provider_choice,
                key_lookup=bot_key_lookup(key),
                key_hint=bot_key_hint(key),
            )
            db.add(setup)
            await db.flush()
        else:
            if nickname and nickname.strip():
                setup.nickname = nickname.strip()
            key = _issue_setup_key(setup)
        await db.commit()
        request.session[f"fresh_connection_key_setup_{setup.id}"] = key
        return RedirectResponse(
            url=f"/me/connections/setup/{setup.id}", status_code=status.HTTP_303_SEE_OTHER
        )
    except OperationalError:
        # Backward compatibility for deployments that haven't created the draft
        # setup table yet. Fall back to the legacy pending connection row so the
        # page keeps working instead of crashing.
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


async def _load_owned_connection_setup(
    db: DbSession, user: User, setup_id: int
) -> ConnectionSetup:
    setup = (
        await db.execute(
            select(ConnectionSetup).where(
                ConnectionSetup.id == setup_id,
                ConnectionSetup.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if setup is None:
        raise HTTPException(status_code=404, detail="Connection setup not found.")
    return setup


@router.get("/setup/{setup_id}", response_class=HTMLResponse)
async def connection_setup_detail(
    setup_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    setup = await _load_owned_connection_setup(db, user, setup_id)
    fresh_key = request.session.get(f"fresh_connection_key_setup_{setup.id}")
    connection = None
    if setup.connection_id is not None:
        connection = await _load_owned_connection(db, user, setup.connection_id)
    return templates.TemplateResponse(
        request,
        "connections/setup.html",
        {
            "user": user,
            "setup": setup,
            "connection": connection,
            "provider_label": _provider_label(setup.provider),
            "fresh_key": fresh_key,
            "setup_message": (
                _setup_message(_provider_label(setup.provider), fresh_key)
                if fresh_key
                else None
            ),
        },
    )


@router.get("/setup/{setup_id}/status", response_class=HTMLResponse)
async def connection_setup_status_fragment(
    setup_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    setup = await _load_owned_connection_setup(db, user, setup_id)
    connection = None
    if setup.connection_id is not None:
        connection = await _load_owned_connection(db, user, setup.connection_id)
    return templates.TemplateResponse(
        request,
        "connections/_setup_status.html",
        {
            "setup": setup,
            "connection": connection,
        },
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
    if fresh_key is not None:
        setup_message = _setup_message(_provider_label(connection.provider), fresh_key)
    else:
        setup_message = None
    return templates.TemplateResponse(
        request,
        "connections/detail.html",
        {
            "user": user,
            "connection": connection,
            "display_name": _connection_display_name(connection),
            "health": health,
            "fresh_key": fresh_key,
            "setup_message": setup_message,
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
