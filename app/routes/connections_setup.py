"""Connection list, creation, and detail routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import String, select
from starlette.responses import Response

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import (
    LIVE_WINDOW_SECONDS,
    ConnectionHealth,
    compute_connection_health,
)
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

# Cap nicknames at the column's declared length so a too-long value returns a
# friendly 400 instead of a Postgres "value too long" 500. Derived from the
# column so it can't drift from the schema.
_NICKNAME_TYPE = ConnectionSetup.__table__.c.nickname.type
_NICKNAME_MAX = (
    _NICKNAME_TYPE.length
    if isinstance(_NICKNAME_TYPE, String) and _NICKNAME_TYPE.length
    else 60
)


def _validate_nickname_length(raw: str | None) -> str | None:
    """Reject a nickname longer than the column holds; otherwise pass through.

    Blank/None handling is intentionally left to ``_ensure_pending_setup_and_key``,
    which strips the value and treats an empty string as "clear the name". We only
    guard the length so a too-long value returns a friendly 400 instead of a
    Postgres "value too long for type character varying(60)" 500.
    """
    if raw is not None and len(raw.strip()) > _NICKNAME_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Nickname must be {_NICKNAME_MAX} characters or fewer.",
        )
    return raw


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


# ---------------------------------------------------------------------------
# Connect options — the single swappable auth seam.
#
# AUTH-AGNOSTIC SEAM (coordination with the parallel `mcp-oauth` workstream):
# The EXACT per-client "add the server" instructions and the Mode A play-prompt
# below MIRROR ``docs/setup-mcp.md`` from the mcp-oauth workstream (worktree
# ``--feat-mcp-oauth``). That doc is the source of truth. The real OAuth flow is
# multi-step, NOT a chained one-liner:
#   1. add the MCP server (header-less — no ``sk_conn_`` key, no ``--header``);
#   2. sign in with Google (interactive — in Claude Code run ``/mcp`` →
#      Authenticate; other clients open a browser on first connect);
#   3. reload;
#   4. paste the play-prompt (``_play_prompt`` below).
# Keep these strings in sync with ``docs/setup-mcp.md`` if that doc changes;
# ``_connect_options`` and ``_play_prompt`` are the only places they live, so the
# swap is a contained change and does not touch layout.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectOption:
    """One provider's "add the server + sign in" instructions for the connect box.

    A provider renders one of two ways:
      - ``kind="command"`` — step 1 is a copyable terminal command in ``command``
        (one paste, even if it's several lines). Sign-in is step 2: if
        ``signin_command`` is set it's a second copyable block (e.g. Claude Code's
        ``/mcp``); otherwise sign-in is automatic and ``signin_note`` just says
        what to expect.
      - ``kind="steps"`` — numbered click-through ``steps`` for GUI providers.
    The play-prompt is the SAME for every provider and is a separate block shown
    after connecting (see ``_play_prompt``), so it is not carried here.
    """

    client_id: str  # stable slug for the CSS-tabs radio inputs
    client_label: str  # human-facing name
    kind: str  # "command" | "steps"
    command: str | None  # kind="command": step 1 copyable terminal command
    signin_title: str | None  # kind="command": step 2 heading (the action, not the effect)
    signin_command: str | None  # kind="command": step 2 copyable command, if any
    signin_note: str | None  # kind="command": what to expect / do for sign-in
    steps: tuple[str, ...]  # kind="steps": numbered click-through steps
    note: str | None  # kind="steps": short footnote under the steps


def _connect_options() -> list[ConnectOption]:
    """Per-client "add the server" options for the state-aware connect box.

    See the AUTH-AGNOSTIC SEAM note above: these mirror ``docs/setup-mcp.md`` from
    the mcp-oauth workstream and are header-less (no key, no ``--header``).
    Providers, in display order: Codex first (the only fully copy-paste, zero-click
    sign-in), then Claude Code, Gemini, Claude Desktop.
    """
    mcp_url = f"{settings.base_url}/mcp"
    return [
        ConnectOption(
            client_id="codex",
            client_label="Codex",
            kind="command",
            # One paste does both: add the server and trigger the sign-in. Pasting
            # both lines into a shell runs them in order, so there's no second step.
            command=(
                f"codex mcp add hoardhurthelp --url {mcp_url}\n"
                "codex mcp login hoardhurthelp"
            ),
            # Codex's one paste does the sign-in too, so step 2 is just the
            # browser approval — "Sign in with Google" is the real action here.
            signin_title="Sign in with Google",
            signin_command=None,
            signin_note="A browser opens — approve the Google sign-in. No key needed.",
            steps=(),
            note=None,
        ),
        ConnectOption(
            client_id="claude-code",
            client_label="Claude Code",
            kind="command",
            command=f"claude mcp add --transport http hoardhurthelp {mcp_url}",
            # Claude Code's sign-in has no shell command — it's the interactive
            # /mcp menu, so /mcp is its own paste (into Claude Code, not the shell).
            # The step's real action is pasting /mcp, so the heading says so.
            signin_title="Paste this into Claude Code",
            signin_command="/mcp",
            signin_note=(
                "Then pick hoardhurthelp and choose Authenticate. A browser opens "
                "— approve the Google sign-in. No key needed."
            ),
            steps=(),
            note=None,
        ),
        ConnectOption(
            client_id="gemini",
            client_label="Gemini",
            kind="command",
            command=f"gemini mcp add hoardhurthelp {mcp_url} --transport http",
            signin_title="Sign in with Google",
            signin_command=None,
            signin_note=(
                "Open Gemini once — it opens a browser to approve. No key needed."
            ),
            steps=(),
            note=None,
        ),
        ConnectOption(
            client_id="claude-desktop",
            client_label="Claude Desktop",
            kind="steps",
            command=None,
            signin_title=None,
            signin_command=None,
            signin_note=None,
            steps=(
                "Settings → Connectors → Add custom connector.",
                f"URL: {mcp_url}",
                "Enable it — Claude Desktop opens a browser to sign in with Google.",
            ),
            note=(
                "Claude Desktop is fine for trying it out, but the CLI or the "
                "always-on connector is steadier for long unattended play."
            ),
        ),
    ]


# The Mode A play-prompt. This MIRRORS the "Mode A" play-prompt block in
# ``docs/setup-mcp.md`` (mcp-oauth workstream) EXACTLY and must stay in sync with
# it. It is the SAME for every client — paste it after the MCP server is added and
# you have signed in with Google. No key or token: the sign-in is on the MCP
# connection itself.
_PLAY_PROMPT = """You are playing Hoard Hurt Help through the hoardhurthelp MCP tools. Play all of
my games on your own until they finish. I'm already signed in on the MCP
connection — never ask me for a key or token.

Loop:
1. Call get_next_turn. It returns my most urgent turn across all my games (the
   game_id/match_id, my strategy, the full move history, the scoreboard, and a
   `current` object with the turn_token and a `phase`), OR a `waiting` status
   with `next_poll_after_seconds`.
2. If status is "your_turn", look at current.phase:
   - phase == "talk": read the messages aimed at me, decide what to say, and call
     submit_talk with that match_id, the turn_token from `current`, and the
     agent_turn_token from the top level. Negotiate — make and answer deals.
   - phase == "act": choose HOARD, HELP, or HURT (HELP/HURT need a target_id),
     write a short message, and call submit_action with that match_id, the
     turn_token, and the agent_turn_token.
3. If status is "waiting", sleep next_poll_after_seconds, then call get_next_turn
   again. get_next_turn long-polls, so a waiting call may take ~25s to return —
   that's expected; just call it again.
4. On a temporary error, wait a few seconds and retry. If a call returns 401 /
   "unauthorized", your sign-in expired — re-authenticate with Google in your
   client, then continue.

Read the chat and history yourself: spot alliances and betrayals and play to my
strategy. Pull get_opponent_history, get_chat, or get_standings only if you need
older detail your client has trimmed. Keep going until every game is over."""


def _play_prompt() -> str:
    """The Mode A play-prompt, pasted after connecting + signing in.

    Mirrors the Mode A play-prompt block in ``docs/setup-mcp.md`` exactly (see the
    AUTH-AGNOSTIC SEAM note) and must stay in sync with it. The same prompt works
    in Claude Code, Claude Desktop, Codex, and Gemini.
    """
    return _PLAY_PROMPT


async def _load_user_agents(db: DbSession, user_id: int) -> list[AgentRow]:
    """All of the user's active AI agents (not bots), newest name first, with
    their current version so the readiness line can show name · model."""
    rows = (
        (
            await db.execute(
                select(Agent, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
                .where(
                    Agent.user_id == user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.archived_at.is_(None),
                )
                .order_by(Agent.name)
            )
        )
        .all()
    )
    return [AgentRow(agent=agent, version=version) for agent, version in rows]


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


def _summarize_agent(agents: list[AgentRow]) -> tuple[bool, str | None]:
    """Whether the user has an AI agent and the "name · model" summary of the first."""
    if not agents:
        return False, None
    first = agents[0]
    model = first.version.model if first.version is not None else None
    summary = f"{first.agent.name} · {model}" if model else first.agent.name
    return True, summary


async def _live_status_context(db: DbSession, user: User) -> dict[str, object]:
    """Shared 'are we live + agent nudge' context for the page and the poll fragment.

    A user is live now if ANY of their non-deleted connections resolves to a LIVE or
    READY health state (running machine, idle-but-ready counts). Reuses the per-row
    health computation so the page and the 4s poll fragment can't drift.
    """
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
    is_live_now = False
    for connection in connections:
        health = await compute_connection_health(db, connection)
        if health.state in (ConnectionHealth.LIVE, ConnectionHealth.READY):
            is_live_now = True
            break
    has_agent, agent_summary = _summarize_agent(await _load_user_agents(db, user.id))
    return {
        "is_live_now": is_live_now,
        "has_agent": has_agent,
        "agent_summary": agent_summary,
        "play_prompt": _play_prompt(),
        "lobby_url": "/games/hoard-hurt-help",
    }


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
    is_live_now = False
    for connection in connections:
        health = await compute_connection_health(db, connection)
        if health.state in (ConnectionHealth.LIVE, ConnectionHealth.READY):
            is_live_now = True
        rows.append(
            {
                "connection": connection,
                "display_name": _connection_display_name(connection),
                "health": health,
                "agents": await _load_attached_agents(db, connection),
            }
        )
    # Three user states drive what the one-box leads with (see the design doc):
    #   NEW       — never connected (no connection rows)
    #   RETURNING — connected before, but none live right now
    #   LIVE      — at least one connection is LIVE or READY right now
    has_connected_before = bool(connections)
    has_agent, agent_summary = _summarize_agent(await _load_user_agents(db, user.id))
    return templates.TemplateResponse(
        request,
        "connections/list.html",
        {
            "user": user,
            "connections": rows,
            "active_setup": active_setup,
            "setup_message": _setup_message(key),
            "connect_options": _connect_options(),
            "play_prompt": _play_prompt(),
            "has_connected_before": has_connected_before,
            "is_live_now": is_live_now,
            "has_agent": has_agent,
            "agent_summary": agent_summary,
            "lobby_url": "/games/hoard-hurt-help",
        },
    )


@router.get("/live-status", response_class=HTMLResponse)
async def live_status_fragment(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    """The self-advancing 'Listening… → you're live' region, polled every 4s.

    Not live → the pulsing "Listening for your AI to connect…" line. Live → the
    post-connect block: a "Create an agent" nudge if the user has no agent, or a
    "Join a game" hand-off to the lobby if they do.
    """
    return templates.TemplateResponse(
        request,
        "connections/_live_status.html",
        await _live_status_context(db, user),
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
    setup, _ = await _ensure_pending_setup_and_key(
        request, db, user.id, nickname=_validate_nickname_length(nickname)
    )
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
