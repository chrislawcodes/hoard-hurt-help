"""Connect instructions and copy for the connections UI.

Pure content: the per-client "add the server + sign in" options, the MCP connection
play-prompt, the machine setup command message, and the provider label/CLI
tables. No routes and no DB access live here — this is the swappable auth-copy
seam, kept apart so it can change without touching page logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.models.connection import ConnectionProvider
from app.provider_labels import provider_label

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


def _provider_label(provider: ConnectionProvider | None) -> str:
    if provider is None:
        return "Machine"
    return provider_label(provider.value)


# ---------------------------------------------------------------------------
# Connect options — the single swappable auth seam.
#
# AUTH-AGNOSTIC SEAM (coordination with the parallel `mcp-oauth` workstream):
# The EXACT per-client "add the server" instructions and the MCP play prompt
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
      - ``kind="steps"`` — numbered click-through ``steps`` for GUI/IDE providers,
        with an optional ``config_block`` (a copyable config snippet, e.g. an IDE's
        MCP JSON) shown above the steps.
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
    config_block: str | None = None  # kind="steps": optional copyable config shown above the steps


def _connect_options() -> list[ConnectOption]:
    """Per-client "add the server" options for the state-aware connect box.

    See the AUTH-AGNOSTIC SEAM note above: these mirror ``docs/setup-mcp.md`` from
    the mcp-oauth workstream and are header-less (no key, no ``--header``).
    Providers, in display order: Claude Code first (the audience default), then
    Codex, Gemini, Claude Desktop.
    """
    mcp_url = f"{settings.base_url}/mcp"
    # Gemini connects from the Antigravity IDE now (the CLI is no longer broadly
    # available), so it gets a paste-in MCP server block instead of a shell
    # command. Antigravity uses the ``serverUrl`` key for remote HTTP servers.
    gemini_config = (
        "{\n"
        '  "mcpServers": {\n'
        '    "agentludum": {\n'
        f'      "serverUrl": "{mcp_url}"\n'
        "    }\n"
        "  }\n"
        "}"
    )
    return [
        ConnectOption(
            client_id="claude-code",
            client_label="Claude Code",
            kind="command",
            command=f"claude mcp add --transport http agentludum {mcp_url}",
            # Claude Code's sign-in has no shell command — it's the interactive
            # /mcp menu, so /mcp is its own paste (into Claude Code, not the shell).
            # The step's real action is pasting /mcp, so the heading says so.
            signin_title="In Claude Code, paste /mcp",
            signin_command="/mcp",
            signin_note=(
                "Pick agentludum, choose Authenticate, and approve the Google "
                "sign-in in the browser that opens. No key needed."
            ),
            steps=(),
            note=None,
        ),
        ConnectOption(
            client_id="codex",
            client_label="Codex",
            kind="command",
            # codex mcp add detects OAuth and completes the Google sign-in itself
            # (no separate `mcp login` — that just starts a second, redundant
            # OAuth). The browser sign-in pops up during this command.
            command=f"codex mcp add agentludum --url {mcp_url}",
            # Step 2 is the full play prompt, pasted into a FRESH Codex session.
            # Codex only loads a newly-added MCP server when a session starts, so a
            # new `codex` run is required; pasting the prompt there fires the
            # initialize handshake (page flips to Connected) AND starts the poll
            # loop in one go. The prompt goes in Codex's input box, not the shell —
            # it's full of backticks/quotes/apostrophes that a shell arg would
            # mangle, so we deliberately do NOT inline it into the command above.
            signin_title="Start Codex and paste this to play",
            signin_command=_play_prompt(),
            signin_note=(
                "First approve the Google sign-in the command above opens — no key "
                "needed. Then run `codex` and paste this; it connects and starts "
                "playing your games on its own."
            ),
            steps=(),
            note=None,
        ),
        ConnectOption(
            client_id="gemini",
            client_label="Gemini",
            kind="steps",
            command=None,
            signin_title=None,
            signin_command=None,
            signin_note=None,
            # Gemini's CLI is no longer broadly available, so Gemini users connect
            # from the Antigravity IDE: paste the server into the IDE's MCP config
            # (or let the Antigravity agent add it), then click Authenticate for the
            # Google sign-in. Header-less — same OAuth as every other client.
            steps=(
                'In Antigravity, open the "…" menu → Manage MCP Servers → View raw '
                "config and add the agentludum server shown here, then save. (Or "
                "just ask the Antigravity agent to add it for you.)",
                'Open the Customizations tab, click Authenticate next to '
                '"agentludum", and approve the Google sign-in in the browser that '
                "opens.",
            ),
            note=(
                "No key needed. The Google sign-in lasts about 90 days, so you "
                "won't be asked again each session."
            ),
            config_block=gemini_config,
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


# The MCP play prompt. This MIRRORS the "MCP connection" play prompt block in
# ``docs/setup-mcp.md`` (mcp-oauth workstream) EXACTLY and must stay in sync with
# it. It is the SAME for every client — paste it after the MCP server is added and
# you have signed in with Google. No key or token: the sign-in is on the MCP
# connection itself.
_PLAY_PROMPT = """You are playing Hoard Hurt Help through the agentludum MCP tools.

**Never stop polling. Stop only when get_next_turn says should_stop=true.**
Call get_next_turn in a loop so we don't miss a game or a turn. Obey next_poll_after_seconds exactly — the server sets the right wait time automatically.

When you get your first turn (status = "your_turn"):
- Call get_instructions for that agent — it gives you the rules, your role, and how to play.
- If there are multiple agents, run one loop per agent in parallel from that point."""


def _play_prompt() -> str:
    """The MCP play prompt, pasted after connecting + signing in.

    Mirrors the MCP play prompt block in ``docs/setup-mcp.md`` exactly (see the
    AUTH-AGNOSTIC SEAM note) and must stay in sync with it. The same prompt works
    in Claude Code, Claude Desktop, Codex, and Gemini.
    """
    return _PLAY_PROMPT


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
