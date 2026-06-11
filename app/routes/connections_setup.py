"""Connection list, creation, and detail routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
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

# The command-line tool the connector looks for to mark a provider "detected".
# Must mirror `_detect_providers()` in scripts/agentludum_connector.py (openai is
# driven by the `codex` CLI). Shown in the install hint when a provider is turned
# on but its CLI isn't found on the machine.
_PROVIDER_CLIS = {
    ConnectionProvider.CLAUDE.value: "claude",
    ConnectionProvider.GEMINI.value: "gemini",
    ConnectionProvider.OPENAI.value: "codex",
    ConnectionProvider.HERMES.value: "hermes",
    ConnectionProvider.OPENCLAW.value: "openclaw",
}

# One connector drives every provider. A connection is a machine; the connector
# auto-detects which AI CLIs are installed and reports them, so there is no
# per-provider setup path or per-provider download anymore.
_SETUP_SCRIPT = "agentludum_connector.py"


@dataclass(frozen=True)
class AgentRow:
    agent: Agent
    version: AgentVersion | None


def _provider_label(provider: ConnectionProvider | None) -> str:
    if provider is None:
        return "Machine"
    return _PROVIDER_LABELS.get(provider.value, provider.value.title())


def _connection_display_name(connection: Connection) -> str:
    return connection.nickname or "Machine"


def _setup_message(key: str) -> str:
    script_name = _SETUP_SCRIPT
    base = settings.base_url
    return (
        "Please connect this machine to Agent Ludum. This is a single standalone "
        "script — you do NOT need any repository, project files, or documentation, "
        "and you do not need to write any service config yourself. Just run the two "
        "commands below.\n\n"
        "Step 1 — download the connector (one file):\n"
        f"  macOS/Linux: mkdir -p ~/.agentludum && curl -fsSL {base}/setup-files/{script_name}"
        f" -o ~/.agentludum/{script_name}\n"
        f"  Windows:     mkdir %USERPROFILE%\\.agentludum && curl -fsSL {base}/setup-files/{script_name}"
        f" -o %USERPROFILE%\\.agentludum\\{script_name}\n\n"
        "Step 2 — install it as a background service (this one command writes the "
        "launchd/systemd/Task Scheduler config, clears macOS download flags, starts "
        "it, and makes it restart on login — you do not set any of that up by hand):\n"
        f"  macOS/Linux: python3 ~/.agentludum/{script_name} --install --key {key} --url {base}\n"
        f"  Windows:     python %USERPROFILE%\\.agentludum\\{script_name} --install --key {key} --url {base}\n\n"
        "On macOS, installing shows a \"Background Items Added\" notice — that is "
        "expected (the connector set to run in the background) and there is nothing "
        "to click. If macOS asks for anything else, you can safely decline; the "
        "connector only needs internet access. Windows and Linux show no such prompt.\n\n"
        "It runs on the AI CLI logins this machine already has and connects every "
        "one it finds (Claude, Gemini, Codex, Hermes, OpenClaw). Do NOT also run it "
        "in the foreground yourself — the service handles that, and a second copy "
        "would just be a duplicate.\n\n"
        "To test without installing a service, run the same command WITHOUT --install "
        "(it runs in the foreground; stop it with Ctrl+C).\n\n"
        "If the server says the key is invalid, stop the service and tell me — "
        "I can rotate it from the connections page."
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



async def _load_resumeable_pending_setup(
    db: DbSession, user_id: int, provider: ConnectionProvider | None
) -> ConnectionSetup | None:
    """Return the newest pending setup for this provider, if one exists."""
    provider_clause = (
        ConnectionSetup.provider.is_(None)
        if provider is None
        else ConnectionSetup.provider == provider
    )
    return (
        await db.execute(
            select(ConnectionSetup)
            .where(
                ConnectionSetup.user_id == user_id,
                provider_clause,
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


async def _ensure_pending_setup_and_key(
    request: Request,
    db: DbSession,
    user_id: int,
    nickname: str | None = None,
) -> tuple[ConnectionSetup, str]:
    """Reuse the user's one open machine setup (or mint it) and return a STABLE
    plaintext key for the inline setup command.

    A machine is provider-agnostic — the connector auto-detects which AI CLIs are
    installed — so setups are always created with ``provider=None``. The key is
    minted once and stashed in the session so reloads show the SAME command; we
    never silently rotate a key the user may have already copied. The key only
    regenerates if the session no longer carries it (e.g. a new browser session),
    since the raw value is unrecoverable from the stored hash.
    """
    setup = await _load_resumeable_pending_setup(db, user_id, None)
    if setup is None:
        key = generate_connection_key()
        setup = ConnectionSetup(
            user_id=user_id,
            nickname=(nickname.strip() if nickname and nickname.strip() else None),
            provider=None,
            key_lookup=bot_key_lookup(key),
            key_hint=bot_key_hint(key),
        )
        db.add(setup)
        await db.flush()
        request.session[f"fresh_connection_key_setup_{setup.id}"] = key
    else:
        if nickname is not None:
            setup.nickname = nickname.strip() or None
        session_field = f"fresh_connection_key_setup_{setup.id}"
        stored = request.session.get(session_field)
        if stored:
            key = str(stored)
        else:
            key = _issue_setup_key(setup)
            request.session[session_field] = key
    await db.commit()
    return setup, key


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
    # The page always offers a ready-to-run setup command inline: reuse the user's
    # one open machine setup or mint it, with a key that stays stable across loads.
    active_setup, key = await _ensure_pending_setup_and_key(request, db, user.id)
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
            "active_setup": active_setup,
            "setup_message": _setup_message(key),
        },
    )


@router.post("/name", response_class=HTMLResponse)
async def save_machine_name(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    nickname: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Auto-save the optional machine name (HTMX, no button, no reload).

    Labels the one open setup; never rotates the key or creates a second setup.
    A blank name is cleared — the machine then names itself from its hostname when
    it connects (see report_pid). Returns a tiny status span for the inline tick.
    """
    setup, _ = await _ensure_pending_setup_and_key(request, db, user.id, nickname=nickname)
    label = "Saved ✓" if setup.nickname else ""
    return HTMLResponse(label)


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
            "setup_message": (_setup_message(fresh_key) if fresh_key else None),
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
            "cli": _PROVIDER_CLIS.get(p.value, p.value),
            "enabled": (provider_rows[p.value].enabled if p.value in provider_rows else False),
            "detected": (provider_rows[p.value].detected if p.value in provider_rows else False),
            "detected_detail": (
                provider_rows[p.value].detected_detail if p.value in provider_rows else None
            ),
        }
        for p in ConnectionProvider
    ]
    setup_message = _setup_message(fresh_key) if fresh_key is not None else None
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
            "provider_models": (
                PROVIDER_MODELS.get(connection.provider.value, [])
                if connection.provider is not None
                else []
            ),
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
