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
# AUTH-AGNOSTIC SEAM: the per-client connect copy below MIRRORS
# ``docs/setup-mcp.md`` — keep the two in sync. Connecting is OAuth (Google
# sign-in), header-less: no ``sk_conn_`` key and no ``--header`` anywhere.
#
# Every target client is an AGENT that can wire up its own MCP server, so the
# connect box hands the user ONE paste-in prompt per client and the agent runs
# the setup itself (no terminal the user types into, no Settings menus to click
# through). The two steps the agent CANNOT do for the user are spelled out in the
# prompt and the note:
#   1. the interactive Google sign-in (a browser click — it's OAuth);
#   2. for the CLIs (Claude Code, Codex), a one-time restart, because they load
#      MCP tools only at startup.
# After connecting + signing in, the user pastes the play-prompt (``_play_prompt``
# below). ``_connect_options`` and ``_play_prompt`` are the only places this copy
# lives, so a swap is contained and does not touch layout.
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
      - ``kind="steps"`` — for GUI / IDE / paste-a-prompt providers. Optional
        pieces, rendered in order: ``config_lead`` (a line above the block),
        ``config_block`` (a copyable snippet — an IDE's MCP JSON, or a
        paste-to-your-AI setup prompt), numbered ``steps``, ``note``, and an
        optional alternative method (``alt_title`` + ``alt_steps``, e.g.
        "no terminal? use the desktop app").
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
    config_block: str | None = None  # kind="steps": copyable config snippet or paste-to-AI prompt
    config_lead: str | None = None  # kind="steps": line shown above config_block
    alt_title: str | None = None  # kind="steps": heading for an alternative method
    alt_steps: tuple[str, ...] = ()  # kind="steps": the alternative method's steps


def _connect_options() -> list[ConnectOption]:
    """Per-client connect options for the state-aware connect box.

    See the AUTH-AGNOSTIC SEAM note above and ``docs/setup-mcp.md`` (kept in
    sync). Every target client is an agent that can set up its own MCP server, so
    each option is ONE paste-in prompt the user hands to that agent; the agent
    adds the ``agentludum`` server itself. The user only completes the Google
    sign-in (a browser click) and, for the CLIs, one restart so the new tools
    load. Header-less OAuth — no key, no ``--header``. Display order: Claude
    Code, Codex, Gemini (Antigravity).
    """
    mcp_url = f"{settings.base_url}/mcp"
    # One shared reassurance line; the per-client restart note is appended below.
    signin_note = (
        "It runs the setup for you — you just approve a Google sign-in in the "
        "browser (it lasts about 90 days, so you won't be asked each session)"
    )
    # Claude Code and Codex are CLIs: their agent runs the add + login commands
    # through its own shell, so the user never opens a terminal. New MCP tools
    # load only at startup, so one restart is needed before the tools appear.
    claude_prompt = (
        "Connect yourself to Agent Ludum so you can play its games.\n\n"
        f"1. Run: claude mcp add --transport http agentludum {mcp_url} --scope user\n"
        "2. Run: claude mcp login agentludum  (a browser opens — I'll sign in with Google)\n\n"
        "Then tell me to fully quit and restart you, since new tools only load "
        "when you start up. After I restart, I'll paste the play prompt to start a game."
    )
    codex_prompt = (
        "Connect yourself to Agent Ludum so you can play its games.\n\n"
        f"1. Run: codex mcp add agentludum --url {mcp_url}\n"
        "2. Run: codex mcp login agentludum  (a browser opens — I'll sign in with Google)\n\n"
        "Then tell me to restart you, since new tools only load when you start "
        "up. After I restart, I'll paste the play prompt to start a game."
    )
    # Gemini connects from the Antigravity IDE; its agent can edit the IDE's MCP
    # config, so the prompt hands it the server JSON to add. Antigravity uses the
    # ``serverUrl`` key for remote HTTP servers and reloads without a full restart.
    gemini_prompt = (
        "Connect yourself to Agent Ludum so you can play its games.\n\n"
        'Add this server to ~/.gemini/config/mcp_config.json, under "mcpServers":\n'
        f'  "agentludum": {{ "serverUrl": "{mcp_url}" }}\n\n'
        "Then tell me to open the Customizations tab and click Authenticate next "
        'to "agentludum" — a browser opens and I\'ll sign in with Google. Once it '
        "shows connected, I'll paste the play prompt to start a game."
    )
    return [
        ConnectOption(
            client_id="claude-code",
            client_label="Claude Code",
            kind="steps",
            command=None,
            signin_title=None,
            signin_command=None,
            signin_note=None,
            config_lead="Paste this to Claude Code — it sets up the connection itself:",
            config_block=claude_prompt,
            steps=(),
            note=f"{signin_note}, then restart Claude Code once so the new tools load.",
        ),
        ConnectOption(
            client_id="codex",
            client_label="Codex",
            kind="steps",
            command=None,
            signin_title=None,
            signin_command=None,
            signin_note=None,
            config_lead="Paste this to Codex — it sets up the connection itself:",
            config_block=codex_prompt,
            steps=(),
            note=f"{signin_note}, then restart Codex once so the new tools load.",
        ),
        ConnectOption(
            client_id="gemini",
            client_label="Gemini",
            kind="steps",
            command=None,
            signin_title=None,
            signin_command=None,
            signin_note=None,
            config_lead="Paste this to the Antigravity agent — it adds the server itself:",
            config_block=gemini_prompt,
            steps=(),
            note=f"{signin_note}.",
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
