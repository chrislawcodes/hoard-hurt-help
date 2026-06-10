"""Connection list, creation, and detail routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.responses import Response

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import LIVE_WINDOW_SECONDS, compute_connection_health
from app.engine.pending_connection_gc import gc_pending_connections
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
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

_SETUP_SCRIPTS = {
    ConnectionProvider.CLAUDE: "agentludum_connector.py",
    ConnectionProvider.GEMINI: "agentludum_connector.py",
    ConnectionProvider.OPENAI: "agentludum_connector.py",
    ConnectionProvider.HERMES: "agentludum_setup_hermes.py",
    ConnectionProvider.OPENCLAW: "agentludum_setup_openclaw.py",
}


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None


def _provider_label(provider: ConnectionProvider) -> str:
    return _PROVIDER_LABELS.get(provider.value, provider.value.title())


def _connection_display_name(connection: Connection) -> str:
    # `connection.provider` is the retained legacy "connection type" (see §1 of
    # the unified-connections spec) — used for the display name fallback, setup
    # script selection, and hermes/openclaw identity. These provider reads are
    # intentional; routing/coverage use connection_providers, not this column.
    return connection.nickname or _provider_label(connection.provider)


def _setup_script_name(provider: ConnectionProvider) -> str:
    return _SETUP_SCRIPTS.get(provider, "agentludum_connector.py")


def _setup_message(provider: ConnectionProvider, key: str) -> str:
    script_name = _setup_script_name(provider)
    return (
        "Please set up my AI connection as a persistent background service that starts "
        "automatically on login and restarts if it ever stops.\n\n"
        "Step 1 — save the setup script to a permanent location:\n"
        "  macOS/Linux: mkdir -p ~/.agentludum && "
        f"curl -fsSL {settings.base_url}/setup-files/{script_name}"
        f" -o ~/.agentludum/{script_name}\n"
        "  Windows:     mkdir %USERPROFILE%\\.agentludum && "
        f"curl -fsSL {settings.base_url}/setup-files/{script_name}"
        f" -o %USERPROFILE%\\.agentludum\\{script_name}\n\n"
        "Step 2 — install a system service so it runs on login and auto-restarts:\n"
        "  macOS:   launchd plist in ~/Library/LaunchAgents/ with KeepAlive=true and RunAtLoad=true\n"
        "  Linux:   systemd user unit (~/.config/systemd/user/) with Restart=on-failure, "
        "then systemctl --user enable --now\n"
        "  Windows: Task Scheduler task triggered on login with restart-on-failure enabled\n\n"
        "Setup command:\n"
        f"  macOS/Linux: python3 ~/.agentludum/{script_name} --key {key} --url {settings.base_url}\n"
        f"  Windows:     python %USERPROFILE%\\.agentludum\\{script_name} --key {key} --url {settings.base_url}\n\n"
        "This setup uses the login I already have. "
        "Use the X-Connection-Key header with that key when the client talks to the server.\n\n"
        "Keep one session per match and only think when it is that agent's turn.\n\n"
        "If the server says the key is invalid, stop the service and tell me. "
        "I can rotate it from the connection page."
    )


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



async def _load_pending_setups(db: DbSession, user_id: int) -> list[ConnectionSetup]:
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
                "agents": await _load_attached_agents(db, connection),
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
                _setup_message(setup.provider, fresh_key)
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
    attached_agents = await _load_attached_agents(db, connection)
    stranded_agents = await _load_stranded_agents(db, user.id)
    provider_rows = await _load_connection_providers(db, connection.id)
    # The toggle box lists every provider with its current enabled/detected state.
    provider_toggles = [
        {
            "value": p.value,
            "label": _provider_label(p),
            "enabled": (provider_rows[p.value].enabled if p.value in provider_rows else False),
            "detected": (provider_rows[p.value].detected if p.value in provider_rows else False),
            "detected_detail": (
                provider_rows[p.value].detected_detail if p.value in provider_rows else None
            ),
        }
        for p in ConnectionProvider
    ]
    setup_message = _setup_message(connection.provider, fresh_key) if fresh_key is not None else None
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
            "stranded_agents": stranded_agents,
            "provider_toggles": provider_toggles,
            "provider_label": _provider_label(connection.provider),
            "provider_models": PROVIDER_MODELS.get(connection.provider.value, []),
            "strand_provider": request.query_params.get("strand_provider"),
            "strand_count": request.query_params.get("strand_count"),
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
            "agent_count": len(await _load_attached_agents(db, connection)),
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
