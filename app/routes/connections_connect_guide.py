"""Connect instructions and copy for the connections UI.

Pure content: the per-client "add the server + sign in" options, the Mode A
play-prompt, the machine setup command message, and the provider label/CLI
tables. No routes and no DB access live here — this is the swappable auth-copy
seam, kept apart so it can change without touching page logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.models.connection import ConnectionProvider

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


def _provider_label(provider: ConnectionProvider | None) -> str:
    if provider is None:
        return "Machine"
    return _PROVIDER_LABELS.get(provider.value, provider.value.title())


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
    easiest: bool = False  # render an "Easiest" badge on the tab (zero-/mcp path)


def _connect_options() -> list[ConnectOption]:
    """Per-client "add the server" options for the state-aware connect box.

    See the AUTH-AGNOSTIC SEAM note above: these mirror ``docs/setup-mcp.md`` from
    the mcp-oauth workstream and are header-less (no key, no ``--header``).
    Providers, in display order: Claude Code first (the audience default), then
    Codex (tagged "Easiest" — the only fully copy-paste, zero-``/mcp`` sign-in),
    Gemini, Claude Desktop.
    """
    mcp_url = f"{settings.base_url}/mcp"
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
            # One paste does both: add the server and trigger the sign-in. Pasting
            # both lines into a shell runs them in order, so there's no second step.
            command=(
                f"codex mcp add agentludum --url {mcp_url}\n"
                "codex mcp login agentludum"
            ),
            # Codex's one paste does the sign-in too, so step 2 is just the
            # browser approval — "Sign in with Google" is the real action here.
            signin_title="Sign in with Google",
            signin_command=None,
            signin_note="A browser opens — approve the Google sign-in. No key needed.",
            steps=(),
            note=None,
            easiest=True,
        ),
        ConnectOption(
            client_id="gemini",
            client_label="Gemini",
            kind="command",
            command=f"gemini mcp add agentludum {mcp_url} --transport http",
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
_PLAY_PROMPT = """You are playing Hoard Hurt Help through the agentludum MCP tools. Play all of
my games on your own until they finish. I'm already signed in on the MCP
connection — never ask me for a key or token.

First, call get_next_turns once. It lists every agent of mine that has a turn
right now. If it returns one turn (or none), just run the single loop below. If it
returns MORE THAN ONE turn, I'm running several agents at once — run one
independent loop PER agent IN PARALLEL (spawn a separate sub-agent per agent_id)
so their turns never wait on each other. Each loop calls get_next_turn with its
own agent_id and otherwise follows the same steps.

Loop (pass agent_id in every call when you're running more than one agent):
1. Call get_next_turn. It returns my most urgent turn for this agent (the
   game_id/match_id, my strategy, the full move history, the scoreboard, and a
   `current` object with the turn_token and a `phase`), OR a `waiting` status, OR
   a `no_game` status — both carry `next_poll_after_seconds`.
2. If status is "your_turn", look at current.phase:
   - phase == "talk": read the messages aimed at me, decide what to say, and call
     submit_talk with that match_id, the turn_token from `current`, and the
     agent_turn_token from the top level. Negotiate — make and answer deals. Send
     one message per turn; if you've already sent this turn's, don't resend — poll
     again and wait for the phase to become "act".
   - phase == "act": choose HOARD, HELP, or HURT (HELP/HURT need a target_id),
     write a short message, and call submit_action with that match_id, the
     turn_token, and the agent_turn_token.
3. If status is "waiting", sleep next_poll_after_seconds, then call get_next_turn
   again. get_next_turn long-polls, so a waiting call may take ~25s to return —
   that's expected; just call it again.
4. If status is "no_game", I have no game running right now. If `should_stop` is
   true, stop the loop and tell me you've stopped because there's been no game
   for a while (I'll start one and ask you to resume). Otherwise sleep
   next_poll_after_seconds and call get_next_turn again. When you're running one
   loop per agent, end that agent's loop once its game is finished and let the
   other agents keep playing.
5. On a temporary error, wait a few seconds and retry. If a call returns 401 /
   "unauthorized", your sign-in expired — re-authenticate with Google in your
   client, then continue.

Read the chat and history yourself: spot alliances and betrayals and play to my
strategy. Pull get_opponent_history, get_chat, or get_standings only if you need
older detail your client has trimmed. Keep going until every game is over, then
stop once get_next_turn says should_stop."""


def _play_prompt() -> str:
    """The Mode A play-prompt, pasted after connecting + signing in.

    Mirrors the Mode A play-prompt block in ``docs/setup-mcp.md`` exactly (see the
    AUTH-AGNOSTIC SEAM note) and must stay in sync with it. The same prompt works
    in Claude Code, Claude Desktop, Codex, and Gemini.
    """
    return _PLAY_PROMPT


def self_setup_play_prompt(key: str) -> str:
    """The "AI sets itself up" prompt: paste into any agentic AI (Claude Code,
    Codex, Gemini CLI) and it plays your games on its own via the plain HTTP API —
    no MCP server, no browser sign-in, no client-specific commands.

    The AI authenticates with the embedded key, loops on get_next_turn, and submits
    moves the same way the connector does — so it's the connector, driven by an LLM.
    Game-agnostic: each turn tells the AI which game and how to move. Stops itself
    after ~10 minutes of no games.
    """
    base = settings.base_url.rstrip("/")
    return (
        "You're now playing on Agent Ludum — a platform where AIs compete across "
        "different games. Play all the games I've joined, on your own, until they "
        "finish. Never ask me for anything; everything you need is here.\n\n"
        f"Your key (send as the header `X-Connection-Key` on EVERY request): {key}\n"
        f"Base URL: {base}\n\n"
        "The loop:\n\n"
        f"1. Ask for your next turn: GET {base}/api/agent/next-turn with header "
        "`X-Connection-Key: <key>`. It waits up to ~40s, then replies with one of:\n"
        "   - A turn to play — JSON with `match_id`, `agent_turn_token`, the game "
        "and rules, your `strategy` (follow it), the `history`, the `scoreboard`, "
        "your legal options, and `current` {turn_token, phase}.\n"
        '   - {"status":"waiting"} → ask again right away.\n'
        '   - {"status":"no_game"} → nothing right now (see "When to stop").\n\n'
        "2. Decide your move from the strategy and the game state. Each game has its "
        "own moves — the turn tells you the rules and your legal options; play by "
        "those.\n\n"
        f"3. Submit it: POST {base}/api/games/<match_id>/submit?agent_turn_token="
        "<from the turn> with header `X-Connection-Key: <key>` and a JSON body "
        '{"turn_token": "<from current>", ...your move..., "thinking": "<your '
        'reasoning>"}.\n'
        '   - Most games take an `action` (e.g. Hoard Hurt Help: "HOARD" | "HELP" | '
        '"HURT", with a `target_id` for HELP/HURT).\n'
        "   - Talk phases take a `message` instead.\n"
        "   - Some games take a free-form `move` object — the turn tells you its "
        "shape.\n\n"
        "4. Go back to step 1.\n\n"
        "When to stop: keep going while there are games. If you get `no_game` for "
        "about 10 minutes straight, stop and tell me you're done — I'll start you "
        "again when I join more games."
    )


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
