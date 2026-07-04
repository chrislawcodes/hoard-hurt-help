#!/usr/bin/env python3
"""Drive a Hoard-Hurt-Help connection as chained agent sessions.

ONE runner for every connection. Each turn the server tells the runner which
agent needs attention and which model that agent is using, and the runner drives
the matching CLI: Claude (``claude``), OpenAI/Codex (``codex``), or Gemini
(``gemini``). The runner keeps one chained session per ``(agent_id, match_id)``
pair so the same connection can field multiple agents without cross-talk.

    python3 agentludum_connector.py --key sk_conn_... --url https://your-site

Optional overrides are still available for local testing:

    python3 agentludum_connector.py --key sk_conn_... --provider gemini
    python3 agentludum_connector.py --key sk_conn_... --model claude-sonnet-4-6

Resolution per turn:
    provider:  --provider flag  >  model prefix  >  legacy payload provider  >  claude
    model:     --model flag      >  turn payload model / legacy preferred_model

Each provider keeps one chained session per agent+match and is fed only the new
events each turn, so the model remembers the whole match and only thinks on that
agent's turn. Runs on your existing CLI login for that provider — no API key. You
need the matching CLI installed and signed in (`claude`, `codex`, or `gemini`).

The per-provider mechanics live in small adapters near the bottom; everything else
(the poll loop, the prompts, the move parsing, the fallback) is shared.
"""

# ------------------------------------------------------------------------
# TABLE OF CONTENTS — search for "SECTION:" to jump between them
#   1. Module setup — compatibility shims and shared tunables
#   2. Turn helpers — move parsing, prompt/history building, and the
#      subprocess runner
#   3. Provider CLI adapters
#   4. Usage & metadata parsing for each CLI's raw output
#   5. Provider/model resolution & CLI detection
#   6. Readiness verification — proactive model health checks
#   7. Decision logic — resolve provider/model, get a move, build the
#      submit request
#   8. OS service install — one command per platform
#   9. Singleton lock — one connector per connection key per machine
#  10. Turn execution — decide, submit, and report one turn
#  11. Poll loop & entry point
# ------------------------------------------------------------------------

from __future__ import annotations

import argparse
import contextvars
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ==============================================================================
# SECTION: Module setup — compatibility shims and shared tunables
# Optional reuse of the game server's canonical protocol/model-allowlist
# constants when run from a source checkout (embedded fallbacks otherwise);
# every timing/concurrency knob for the poll loop; the shared HTTP client
# (`_http()`); the chat protocol text; and the per-session/per-process
# token-usage state (`_GameSession`).
# ==============================================================================

# Standalone fallback values for the enforced move-text caps — used when app/ is not
# importable (the real situation on operator machines, where this script is copied and
# run on its own). tests/test_move_length_limits.py pins these to the server's
# authoritative caps (app.agent_prompt.MESSAGE_MAX_LENGTH / THINKING_MAX_LENGTH) so a
# divergence fails CI before it can silently drop a move.
_FALLBACK_MESSAGE_MAX_LENGTH = 200
_FALLBACK_THINKING_MAX_LENGTH = 200

try:
    # In a source checkout, reuse the game's canonical protocol + cap constants.
    # Downloaded standalone connectors use the embedded compatibility copies below.
    from app.agent_prompt import RESPONSE_PROTOCOL as _CANONICAL_PROTOCOL
    from app.agent_prompt import MESSAGE_MAX_LENGTH as _MESSAGE_MAX_LENGTH
    from app.agent_prompt import THINKING_MAX_LENGTH as _THINKING_MAX_LENGTH
except ImportError:
    _CANONICAL_PROTOCOL = None
    _MESSAGE_MAX_LENGTH = _FALLBACK_MESSAGE_MAX_LENGTH
    _THINKING_MAX_LENGTH = _FALLBACK_THINKING_MAX_LENGTH

# In a source checkout, resolve model→provider through the game's authoritative
# allowlist (app.config.PROVIDER_MODELS) instead of the connector's own prefix
# heuristic. The two can disagree — a model with a known prefix that is NOT a real
# model of that provider (e.g. a freeform Hermes/OpenClaw model, or a not-yet-listed
# name) is mis-attributed by the prefix guess but correctly returns None from the
# allowlist. A stale prefix mapping caused a real production incident (#569).
# Standalone operator copies (no `app` package) fall back to the prefix heuristic.
try:
    from app.config import provider_for_model as _authoritative_provider_for_model
except ImportError:
    _authoritative_provider_for_model = None

DEFAULT_URL = "http://localhost:8000"
DEFAULT_PROVIDER = "claude"
_TURN_TIMEOUT = 180  # absolute ceiling for a single model turn

# Each turn phase has a hard server-side deadline (`current.deadline`); a move
# POSTed after it is rejected with 410 DEADLINE_PASSED, so the agent defaults.
# We therefore budget the model call against the time actually left in the phase
# instead of always allowing the full _TURN_TIMEOUT: reserve _SUBMIT_BUFFER_SECONDS
# for the POST round-trip, and if fewer than _MIN_MODEL_SECONDS remain we skip the
# model entirely and submit the fallback so *something* lands before the deadline.
# If the deadline has ALREADY passed, the worker returns without submitting (a POST
# would just 410); the in-flight guard frees the session and the next poll picks up
# the next live phase, so the runner never busy-loops re-POSTing doomed fallbacks.
_SUBMIT_BUFFER_SECONDS = 8
_MIN_MODEL_SECONDS = 6

# The runner drives every servable turn concurrently (one worker per agent+match
# session) instead of one model call at a time — a slow call in match A no longer
# burns match B's deadline. _MAX_CONCURRENCY caps simultaneous model subprocesses
# so we don't overload the machine or trip provider rate limits. _POLL_INTERVAL is
# how often we re-poll while work is in flight, to pick up newly-opened phases.
_MAX_CONCURRENCY = 4
_POLL_INTERVAL_SECONDS = 3

# Guards the process-wide token tally in _record_usage; per-session state is safe
# without it because exactly one worker ever touches a given session at a time.
_usage_lock = threading.Lock()

# Per-turn model-call budget (seconds), set by _decide and read by _run so every
# provider adapter is bounded without threading a timeout through each one.
_call_timeout: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "call_timeout", default=None
)

# One pooled HTTP client for the whole run. The connector is a long-lived daemon
# that polls every few seconds and POSTs every turn; a single shared client keeps
# the connection alive and reuses it, instead of doing a fresh TCP+TLS handshake
# on every call. Created lazily so importing this module (e.g. in tests) opens no
# sockets; httpx.Client is safe to share across the poll loop, the turn workers,
# and the verification thread.
_http_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client()
    return _http_client


# Circuit-breaker threshold for the poll loop. Each failed poll sleeps ~5 s, so
# 24 consecutive failures ≈ 2 minutes of a permanently unreachable server before
# we give up and exit with a non-zero code.
_POLL_FAIL_THRESHOLD = 24

# How long to idle between polls once the key is revoked/deleted (401/410). We do
# NOT exit on those: under launchd/systemd auto-restart, exiting just respawns us
# to hit the same status in seconds — a churning, log-spamming spin. Idling slowly
# instead keeps the cost near zero until a reinstall replaces this process.
_AUTH_BLOCKED_SLEEP_SECONDS = 60

# How often the connector re-reports its PID + detected provider CLIs while
# running. Detection is otherwise a one-shot at startup, so installing a CLI (or
# turning a provider on) would not show as "detected" until the next restart.
# Re-reporting on this interval lets the website reflect a freshly installed CLI
# within a few minutes without the operator restarting the connector.
_DETECT_REPORT_INTERVAL = 300  # seconds
# Model verification (fail-fast): when idle, pull the worklist and test each model
# at most this often, with a short per-call timeout. Runs only on the idle branch
# so it never delays a live turn.
_VERIFY_INTERVAL = 60  # seconds
_VERIFY_TIMEOUT = 30  # seconds per test call

_PROTOCOL = _CANONICAL_PROTOCOL or """TALK PHASE response:
{"message": "<public message, max 200 chars>", "thinking": "<private reasoning, max 200 chars>"}

ACT PHASE response:
{"action": "HOARD|HELP|HURT", "target_id": "<another agent ID for HELP/HURT; null for HOARD>", "thinking": "<private reasoning, max 200 chars>"}

Return exactly one JSON object with no prose or code fence. Use one short, non-empty sentence for `thinking`.

Each phase has a hard deadline, and the turn prompt tells you the approximate seconds left. Decide and answer immediately. A late reply is discarded and counts as a missed move."""
_ENGAGE = (
    "The chat is part of the game: read the other agents' messages, answer "
    "what's aimed at you, make and weigh deals, build or break alliances — "
    "let their words shape your move."
)

# Claude/Codex usage maps onto these four billing buckets. On a resumed session
# most input should land in `cache_read`; if `fresh_in`/`cache_write` stay large
# every turn the prefix is NOT being reused.
_TOKEN_KEYS = ("fresh_in", "cache_write", "cache_read", "out")
_session_tokens: dict[str, int] = {k: 0 for k in _TOKEN_KEYS}


@dataclass
class _GameSession:
    """One chained session per agent+match: provider/model and the CLI session."""

    token: str | None = None  # session_id (claude) / thread_id (codex) / UUID (gemini)
    last_marker: tuple[int, int] = (0, 0)  # max (round, turn) already told the model
    provider: str | None = None
    model: str | None = None
    tokens: dict[str, int] = field(default_factory=lambda: {k: 0 for k in _TOKEN_KEYS})


# ==============================================================================
# SECTION: Turn helpers — move parsing, prompt/history building, and the
# subprocess runner
# Validates and normalizes the model's move; builds the system-prompt framing
# and the first-turn vs. delta-turn message bodies sent to every adapter; and
# runs each provider CLI in a neutral workspace dir.
# ==============================================================================


def _parse_move(text: str) -> dict:
    """Pull the move JSON out of the model's reply (tolerates code fences/prose)."""
    text = re.sub(r"^```[a-z]*\n?", "", text.strip()).rstrip("` \n")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise RuntimeError(f"model returned no JSON move:\n{text[:300]}")


def _phase(cur: dict) -> str:
    return str(cur.get("phase", "act")).lower()


def _format_talk_messages(cur: dict) -> str:
    return json.dumps(cur.get("talk_messages", []), separators=(",", ":"))


def _phase_suffix(cur: dict) -> str:
    clock = _time_left_note(cur)
    if _phase(cur) == "talk":
        return f"TALK PHASE — JSON only.{clock}"
    # A short per-turn nudge of the ACT rule — NOT the full agent list, which is
    # already in this turn's scoreboard/messages. In a chained session the
    # "HELP/HURT need a target" rule fades from the first turn's system prompt and
    # the model intermittently drops target_id (→ server 400). This one line keeps
    # most moves valid; a rare miss is caught by the re-ask in `_decide`.
    return (
        f"ACT PHASE — this turn's messages: {_format_talk_messages(cur)}. "
        f"Decide your action, JSON only; HELP/HURT need a target_id (HOARD: null).{clock}"
    )


def _time_left_note(cur: dict) -> str:
    """A short ' You have ~Ns to reply.' note from the phase deadline, or '' if
    the payload carries no deadline (older servers)."""
    budget = _phase_time_budget(cur)
    if budget is None:
        return ""
    return f" You have ~{max(0, int(budget))}s to reply — answer now."


def _clip(text: object, limit: int) -> str:
    return str(text or "")[:limit]


def _default_move(phase: str) -> dict:
    if phase == "talk":
        return {"message": "", "thinking": ""}
    return {"action": "HOARD", "target_id": None, "thinking": ""}


def _phase_time_budget(cur: dict, *, now: datetime | None = None) -> float | None:
    """Seconds left for the model call before this phase's deadline.

    Reads ``current.deadline`` (ISO 8601) and subtracts _SUBMIT_BUFFER_SECONDS so
    the move can still be POSTed in time. Returns None when no parseable deadline
    is present (older servers) — callers then fall back to the full _TURN_TIMEOUT.
    """
    raw = cur.get("deadline")
    if not raw:
        return None
    try:
        deadline = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (deadline - current).total_seconds() - _SUBMIT_BUFFER_SECONDS


def _normalize_move(move: dict, phase: str) -> dict:
    if phase == "talk":
        return {
            "message": _clip(move.get("message", ""), _MESSAGE_MAX_LENGTH),
            "thinking": _clip(move.get("thinking", ""), _THINKING_MAX_LENGTH),
        }
    return {
        "action": str(move.get("action", "HOARD")).upper(),
        "target_id": move.get("target_id") or None,
        "thinking": _clip(move.get("thinking", ""), _THINKING_MAX_LENGTH),
    }


def _valid_target_ids(turn: dict) -> list[str]:
    """The agent IDs this player may target (everyone but itself), from the
    payload's static block — the same identifiers the model is shown."""
    static = turn.get("static", {})
    you = static.get("your_agent_id")
    return [a for a in static.get("all_agent_ids", []) if a != you]


def _target_is_valid(target: object, valid_ids: list[str]) -> bool:
    """True if `target` names a real other agent, tolerating case and surrounding
    whitespace (mirrors the server's forgiving match so we don't re-ask for a
    target the server would have accepted)."""
    if not target:
        return False
    needle = str(target).strip().casefold()
    return any(needle == str(v).strip().casefold() for v in valid_ids)


def _retarget_body(action: str, valid_ids: list[str], cur: dict) -> str:
    """A corrective re-prompt for when the model chose HELP/HURT but gave no valid
    target. Restates the requirement and the exact allowed list, and asks again."""
    return (
        f"Your last reply chose {action} but target_id was missing or was not one of "
        f"the other agents. {action} REQUIRES a target_id chosen from this exact list: "
        f"{valid_ids}. Reply again with exactly one JSON object: "
        f'{{"action":"{action}","target_id":"<one of {valid_ids}>","thinking":"<short reason>"}} '
        f"— JSON only, no code fence.{_time_left_note(cur)}"
    )


def _sum_usage(
    a: dict[str, int] | None, b: dict[str, int] | None
) -> dict[str, int] | None:
    """Combine two token-usage tallies (either may be None), for when a turn makes
    more than one model call (the target re-ask)."""
    if a is None:
        return b
    if b is None:
        return a
    return {k: a.get(k, 0) + b.get(k, 0) for k in _TOKEN_KEYS}


def _framing(turn: dict) -> str:
    """The stable per-game framing (strategy + rules + protocol). Claude sends it
    as a `--system-prompt`; Codex/Gemini fold it into the first message."""
    static = turn["static"]
    strategy = static.get("your_strategy") or "Play to win."
    base_prompt = static.get("base_prompt")
    if base_prompt:
        return (
            f"{base_prompt}\n\n"
            f"YOUR STRATEGY (this is your strategy - play it):\n{strategy}"
        )

    # Compatibility with servers that predate `static.base_prompt`.
    you = static["your_agent_id"]
    others = [a for a in static.get("all_agent_ids", []) if a != you]
    return (
        f'You are playing Hoard-Hurt-Help as agent "{you}" — a multi-round game '
        f"you play to its end. {_ENGAGE}\n\n"
        f"YOUR STRATEGY (this is your strategy — play it):\n{strategy}\n\n"
        f"RULES:\n{static.get('rules', '')}\n\n"
        f"Agents you may target: {others}\n\n{_PROTOCOL}"
    )


def _setup_body(turn: dict) -> str:
    """First-message body: the full game state so far + whose turn it is. The
    framing is supplied separately (the adapter decides where it goes)."""
    cur = turn["current"]
    return (
        "GAME SO FAR — SCOREBOARD:\n"
        f"{json.dumps(turn.get('scoreboard', []), separators=(',', ':'))}\n"
        "HISTORY (oldest to newest):\n"
        f"{json.dumps(turn.get('history', []), separators=(',', ':'))}\n\n"
        f"It is now round {cur['round']}, turn {cur['turn']}. {_phase_suffix(cur)}"
    )


def _delta_body(new_history: list, scoreboard: list, cur: dict) -> str:
    """Later-message body: only what's resolved since the model's last move."""
    return (
        "Since your last move:\n"
        f"NEW EVENTS:\n{json.dumps(new_history, separators=(',', ':'))}\n"
        f"SCOREBOARD:\n{json.dumps(scoreboard, separators=(',', ':'))}\n\n"
        f"It is now round {cur['round']}, turn {cur['turn']}. {_phase_suffix(cur)}"
    )


def _fetch_full_history(base: str, match_id: str) -> tuple[list, list] | None:
    """The whole resolved transcript + scoreboard for a match, or None on failure.

    The per-turn poll payload now carries only a small rolling window of history
    (the server stopped re-sending the whole transcript every poll). A chained
    session is primed ONCE with the full game so far, so when we open a fresh
    session mid-game we pull the rest here instead, from the public spectator
    state. Mapped to the same shape as the poll payload's `history` so the model
    sees one consistent format across the priming message and later deltas.

    fail-open: advisory only — if the pull fails we return None and the caller
    primes with the windowed history it already has. The agent still plays; it
    just starts with less of the early game in view.
    """
    try:
        resp = _http().get(f"{base}/api/spectator/games/{match_id}/state", timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(
            f"[agentludum-connector] WARNING: could not fetch full history for "
            f"{match_id} ({exc}); priming with the recent-turns window instead.",
            file=sys.stderr,
        )
        return None
    history: list[dict] = []
    for turn in data.get("history", []):
        message_by_agent = {
            msg["agent_id"]: msg.get("message", "")
            for msg in turn.get("messages", [])
        }
        history.append(
            {
                "round": turn["round"],
                "turn": turn["turn"],
                "actions": [
                    {
                        "agent_id": action["agent_id"],
                        "action": action["action"],
                        "target_id": action.get("target_id"),
                        "message": message_by_agent.get(action["agent_id"], ""),
                        "points_delta": action.get("points_delta", 0),
                    }
                    for action in turn.get("actions", [])
                ],
            }
        )
    return history, data.get("scoreboard", [])


# The AI CLIs run here, not wherever the operator happened to launch the
# connector. A CLI that inspects "the working directory" then sees a neutral
# scratch folder inside our own dotfolder — never the operator's Desktop /
# Documents / Downloads, which on macOS would pop a file-access prompt blamed on
# "Python" (the CLIs are our child processes). Created on demand; safe to remake.
_WORKSPACE_DIR = Path.home() / ".agentludum" / "workspace"


def _workspace_dir() -> str:
    """Path to the neutral scratch dir the AI CLIs run in, created if missing."""
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    return str(_WORKSPACE_DIR)


def _run(argv: list[str], *, stdin_input: str | None = None) -> subprocess.CompletedProcess:
    """Run a CLI once. Prompt via stdin (claude) or argv (codex/gemini); when no
    stdin is piped we feed DEVNULL so the CLI never blocks waiting on input. The
    CLI runs in a neutral workspace dir so it never scans the operator's real
    folders."""
    cwd = _workspace_dir()
    budget = _call_timeout.get()
    timeout = _TURN_TIMEOUT if budget is None else max(1.0, min(budget, _TURN_TIMEOUT))
    if stdin_input is not None:
        return subprocess.run(
            argv, input=stdin_input, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
    return subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=timeout, cwd=cwd,
    )


# ==============================================================================
# SECTION: Provider CLI adapters
# One class per CLI (claude / codex / gemini / hermes / openclaw); each turns
# a turn payload into a subprocess call and parses the reply. Changed most
# often: adapter arg lists when a CLI's flags change.
# ==============================================================================


class _ClaudeAdapter:
    """`claude --print` with `--system-prompt` for framing and `--resume` for the
    chained session. Captures the session id Claude hands back. Reports usage."""

    cli = "claude"
    default_model = "claude-haiku-4-5"

    def _call(self, argv: list[str], body: str) -> dict:
        proc = _run(argv, stdin_input=body)
        # `claude --output-format json` reports API failures inside the stdout
        # JSON (is_error / api_error_status / result), usually with a non-zero
        # exit AND an empty stderr. Prefer that structured reason: a bare
        # "claude exit 1" hides an auth/model failure (e.g. a 401) and blinds the
        # fail-fast classifier, which keys off the error text, to what went wrong.
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("is_error"):
            status = data.get("api_error_status")
            detail = str(data.get("result", "")).strip() or (
                f"claude API error {status}" if status else ""
            )
            raise RuntimeError(
                detail or proc.stderr.strip() or f"claude exit {proc.returncode}"
            )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"claude exit {proc.returncode}")
        if data is None:
            raise RuntimeError(f"claude returned non-JSON: {proc.stdout[:300]}")
        return data

    def first(self, *, body: str, framing: str, model: str, session: _GameSession):
        data = self._call(
            ["claude", "--print", "--output-format", "json", "--model", model,
             "--tools", "", "--system-prompt", framing],
            body,
        )
        session.token = data.get("session_id")
        return str(data.get("result", "")), _claude_usage(data)

    def resume(self, *, body: str, model: str, session: _GameSession):
        data = self._call(
            ["claude", "--print", "--output-format", "json", "--model", model,
             "--tools", "", "--resume", str(session.token)],
            body,
        )
        return str(data.get("result", "")), _claude_usage(data)


class _CodexAdapter:
    """`codex exec` / `codex exec resume <thread>`. Framing folds into the first
    message; `--output-last-message` gives us the final answer cleanly."""

    cli = "codex"
    default_model = "gpt-5.4-mini"

    def _call(
        self, resume_id: str | None, model: str, prompt: str
    ) -> tuple[str, str, dict[str, int] | None]:
        # read-only sandbox: a game move needs no file writes and no network, so
        # lock the model's shell tool to reads only. (Codex writes the
        # --output-last-message file itself, outside the sandbox, so capture
        # still works.) `--sandbox` must come BEFORE the `resume` subcommand —
        # `codex exec resume` rejects it as an unknown argument (codex >= 0.142),
        # so passing it after `resume <id>` failed every resumed turn.
        argv = ["codex", "exec", "--sandbox", "read-only"]
        if resume_id:
            argv += ["resume", resume_id]
        argv += ["--json", "--skip-git-repo-check", "--model", model]
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "last_message.txt"
            argv += ["--output-last-message", str(out_file), prompt]
            proc = _run(argv)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f"codex exit {proc.returncode}")
            tid = resume_id or _thread_id_from_jsonl(proc.stdout)
            if tid is None:
                raise RuntimeError(f"codex did not report a thread_id:\n{proc.stdout[:300]}")
            usage = _codex_usage(proc.stdout)
            try:
                answer = out_file.read_text().strip()
            except OSError as exc:
                raise RuntimeError(f"could not read codex output file: {exc}") from exc
        if not answer:
            raise RuntimeError(f"codex returned an empty final message:\n{proc.stdout[:300]}")
        return answer, tid, usage

    def first(self, *, body: str, framing: str, model: str, session: _GameSession):
        text, session.token, usage = self._call(None, model, f"{framing}\n\n{body}")
        return text, usage

    def resume(self, *, body: str, model: str, session: _GameSession):
        text, _, usage = self._call(str(session.token), model, body)
        return text, usage


class _GeminiAdapter:
    """`gemini -p` with a UUID we assign via `--session-id`, then `--resume <uuid>`.
    Framing folds into the first message. (No prefix caching, so long games are
    pricier here.)"""

    cli = "gemini"
    default_model = "gemini-3-flash-preview"

    def _call(
        self, session_id: str, model: str, prompt: str, *, resume: bool
    ) -> tuple[str, dict[str, int] | None]:
        argv = ["gemini", "-p", prompt]
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
        argv += ["--output-format", "json", "--skip-trust", "-m", model]
        proc = _run(argv)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"gemini exit {proc.returncode}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"gemini returned non-JSON: {proc.stdout[:300]}") from exc
        response = data.get("response")
        if not response:
            raise RuntimeError(f"gemini returned no response field:\n{proc.stdout[:300]}")
        return str(response), _gemini_usage(data)

    def first(self, *, body: str, framing: str, model: str, session: _GameSession):
        session.token = str(uuid.uuid4())
        return self._call(session.token, model, f"{framing}\n\n{body}", resume=False)

    def resume(self, *, body: str, model: str, session: _GameSession):
        return self._call(str(session.token), model, body, resume=True)


class _HermesAdapter:
    """`hermes -z` one-shot (NousResearch/hermes-agent). `-z` is the headless
    print mode: single prompt in, final reply text out, exit.

    Path A: Hermes has no captured session here, so it is fed the FULL game state
    every turn (``supports_resume = False`` makes ``_decide`` always send
    ``_setup_body``). It uses its OWN configured model — the connector never
    passes ``--model``. Adding ``--resume`` delta turns is a follow-up once a
    live install confirms how ``-z`` exposes the session id.
    """

    cli = "hermes"
    # Hermes uses its own configured model; this is a placeholder so `_resolve`'s
    # `adapter.default_model` access works. The adapter never passes it to the CLI.
    default_model = "hermes"
    supports_resume = False

    def _one_shot(self, prompt: str) -> str:
        proc = _run(["hermes", "-z", prompt])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"hermes exit {proc.returncode}")
        text = proc.stdout.strip()
        if not text:
            raise RuntimeError(
                f"hermes -z returned empty output:\n{proc.stderr[:300]}"
            )
        return text

    def first(self, *, body: str, framing: str, model: str, session: _GameSession):
        # Leave session.token None on purpose: every turn re-sends the full state.
        return self._one_shot(f"{framing}\n\n{body}"), None

    def resume(self, *, body: str, model: str, session: _GameSession):
        # Unreachable while supports_resume is False (`_decide` always calls
        # first); kept for interface symmetry.
        return self._one_shot(body), None


class _OpenClawAdapter:
    """`openclaw agent --message` one-shot. A one-shot run replies once and the
    child retires, so there is no lingering process.

    Path A (same as Hermes): no captured session, so it is fed the FULL game
    state every turn (``supports_resume = False``). It uses its OWN configured
    default model — the connector never passes ``--model``. Adding thread/resume
    delta turns is a follow-up once a live install confirms how a one-shot
    exposes its thread id.
    """

    cli = "openclaw"
    # OpenClaw uses its own configured model; this is a placeholder so `_resolve`'s
    # `adapter.default_model` access works. The adapter never passes it to the CLI.
    default_model = "openclaw"
    supports_resume = False

    def _one_shot(self, prompt: str) -> str:
        proc = _run(["openclaw", "agent", "--message", prompt])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"openclaw exit {proc.returncode}")
        text = proc.stdout.strip()
        if not text:
            raise RuntimeError(
                f"openclaw agent returned empty output:\n{proc.stderr[:300]}"
            )
        return text

    def first(self, *, body: str, framing: str, model: str, session: _GameSession):
        # Leave session.token None on purpose: every turn re-sends the full state.
        return self._one_shot(f"{framing}\n\n{body}"), None

    def resume(self, *, body: str, model: str, session: _GameSession):
        # Unreachable while supports_resume is False (`_decide` always calls
        # first); kept for interface symmetry.
        return self._one_shot(body), None


# Adapter registry keyed by the server's provider value.
_ADAPTERS: dict[
    str,
    _ClaudeAdapter | _CodexAdapter | _GeminiAdapter | _HermesAdapter | _OpenClawAdapter,
] = {
    "claude": _ClaudeAdapter(),
    "openai": _CodexAdapter(),
    "gemini": _GeminiAdapter(),
    "hermes": _HermesAdapter(),
    "openclaw": _OpenClawAdapter(),
}


# ==============================================================================
# SECTION: Usage & metadata parsing for each CLI's raw output
# Pulls the thread/session id and token-usage numbers out of each CLI's
# stdout (Claude's JSON, Codex's JSONL, Gemini's stats block), and tallies
# them into the running per-session and per-process totals.
# ==============================================================================


def _thread_id_from_jsonl(stdout: str) -> str | None:
    """Pull `thread_id` from the first `thread.started` event in Codex's JSONL."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            tid = event.get("thread_id")
            if tid:
                return str(tid)
    return None


def _claude_usage(data: dict) -> dict[str, int]:
    u = data.get("usage", {}) or {}
    return {
        "fresh_in": u.get("input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "out": u.get("output_tokens", 0),
    }


def _codex_usage(stdout: str) -> dict[str, int] | None:
    """Extract Codex usage from its JSONL stream."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        raw_usage = event.get("usage") or {}
        if not isinstance(raw_usage, dict):
            return None
        input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
        cached_input_tokens = int(raw_usage.get("cached_input_tokens", 0) or 0)
        output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
        reasoning_output_tokens = int(raw_usage.get("reasoning_output_tokens", 0) or 0)
        return {
            "fresh_in": max(input_tokens - cached_input_tokens, 0),
            "cache_write": 0,
            "cache_read": max(cached_input_tokens, 0),
            "out": max(output_tokens + reasoning_output_tokens, 0),
        }
    return None


def _gemini_usage(data: dict) -> dict[str, int] | None:
    """Extract Gemini usage from the `stats` block."""
    stats = data.get("stats", {})
    models = stats.get("models", {}) if isinstance(stats, dict) else {}
    if not isinstance(models, dict) or not models:
        return None
    model_stats = next(iter(models.values()))
    if not isinstance(model_stats, dict):
        return None
    tokens = model_stats.get("tokens") or {}
    if not isinstance(tokens, dict):
        return None
    input_tokens = int(tokens.get("input", 0) or 0)
    cached_tokens = int(tokens.get("cached", 0) or 0)
    total_tokens = int(tokens.get("total", 0) or 0)
    return {
        "fresh_in": max(input_tokens - cached_tokens, 0),
        "cache_write": 0,
        "cache_read": max(cached_tokens, 0),
        "out": max(total_tokens - input_tokens, 0),
    }


def _record_usage(game_id: str, cur: dict, usage: dict[str, int], sess: _GameSession) -> None:
    with _usage_lock:
        for k in _TOKEN_KEYS:
            sess.tokens[k] += usage[k]
            _session_tokens[k] += usage[k]

    def _fmt(t: dict[str, int]) -> str:
        return " ".join(f"{k}={t[k]}" for k in _TOKEN_KEYS)

    print(
        f"[agentludum-connector] {game_id} R{cur['round']}T{cur['turn']} this call: {_fmt(usage)} | "
        f"game total: {_fmt(sess.tokens)} | all games: {_fmt(_session_tokens)}"
    )


# ==============================================================================
# SECTION: Provider/model resolution & CLI detection
# Keys a turn to its (agent_id, match_id) session; maps a model name to its
# provider; and reports which provider CLIs are installed on this machine
# (informational only — never flips the operator's enabled toggle).
# ==============================================================================


def _turn_match_id(turn: dict) -> str:
    return str(turn.get("match_id") or turn.get("game_id") or "")


def _session_key(turn: dict) -> tuple[str, str]:
    agent_id = str(turn.get("agent_id") or "")
    match_id = _turn_match_id(turn)
    if not agent_id or not match_id:
        raise ValueError("turn payload must include agent_id and match_id")
    return agent_id, match_id


def _provider_from_model(model: str | None) -> str | None:
    model = (model or "").lower()
    if _authoritative_provider_for_model is not None:
        # Source checkout: defer to the game's authoritative allowlist. Returns
        # None for a model in no allowlist (e.g. freeform Hermes/OpenClaw), which
        # the caller then resolves from the stored provider instead of guessing.
        return _authoritative_provider_for_model(model)
    # Standalone operator copy (no `app` package): prefix heuristic fallback.
    if model.startswith("claude-"):
        return "claude"
    if model.startswith("gpt-"):
        return "openai"
    if model.startswith("gemini-"):
        return "gemini"
    return None


def _detect_providers() -> list[str]:
    """Report which provider CLIs are installed on this machine.

    Maps each provider to its CLI binary (openai is driven by the `codex` CLI).
    Best-effort and informational only — the server stores these into
    connection_providers.detected; it never flips the user's `enabled` toggle.
    """
    cli_by_provider = {
        "claude": "claude",
        "openai": "codex",
        "gemini": "gemini",
        "hermes": "hermes",
        "openclaw": "openclaw",
    }
    return [provider for provider, cli in cli_by_provider.items() if shutil.which(cli)]


def _report_pid(base: str, headers: dict[str, str], pid: int) -> None:
    """Best-effort report of this process's PID + detected provider CLIs.

    Called once at startup and then on _DETECT_REPORT_INTERVAL so a CLI the
    operator installs while the connector runs becomes "detected" on the site
    without a restart. Non-fatal: the connector keeps playing if the report
    fails.
    """
    try:
        _http().post(
            f"{base}/api/agent/report-pid",
            headers=headers,
            json={
                "pid": pid,
                "hostname": socket.gethostname(),
                "detected_providers": _detect_providers(),
            },
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError as exc:
        print(
            f"[agentludum-connector] WARNING: could not report PID {pid} to server: {exc}",
            file=sys.stderr,
        )


# ==============================================================================
# SECTION: Readiness verification — proactive model health checks
# Runs a cheap test call per configured model while the connector is idle
# (never during a live turn) and reports verified/failed/timeout up to the
# server, so a broken login or missing model shows up before it costs a turn.
# ==============================================================================

# Stderr markers that mean "this login genuinely can't run this model" → sticky
# FAILED. Anything else (timeout, CLI missing, network, odd output) is retryable
# TIMEOUT — the conservative default, so a blip never reads as a permanent fault.
_MODEL_UNAVAILABLE_MARKERS = (
    "not found",
    "404",
    "unauthorized",
    "not available",
    "no access",
    "does not exist",
    "invalid model",
    "unknown model",
    # NB: no bare "permission" — it false-matches a local "Permission denied"
    # file error (e.g. the codex output-file wrapper), which is transient, not a
    # model fault. Genuine auth failures are caught by "unauthorized"/"no access".
)

# A signed-out CLI can exit 0 yet print a login nudge to stdout — that is NOT a
# pass. These markers (in either stream) force a FAILED so a logged-out provider
# can't masquerade as a verified model.
_NOT_LOGGED_IN_MARKERS = (
    "login",
    "log in",
    "sign in",
    "signed out",
    "not authenticated",
    "authenticate",
)


def _should_verify(now: float, last_verify: float) -> bool:
    """Pure cadence gate: has it been at least _VERIFY_INTERVAL since last verify?"""
    return now - last_verify >= _VERIFY_INTERVAL


def _classify_verify(
    returncode: int, stdout: str, stderr: str, timed_out: bool
) -> str:
    """Classify a verification test call into verified / failed / timeout.

    verified = clean exit with some output (a runnability check — looser than the
    move-parse path, so a model that runs but returns non-JSON still counts).
    failed = a clear model-unavailable/unauthorized signal (sticky). Everything
    else is a retryable timeout.
    """
    if timed_out:
        return "timeout"
    both = f"{stdout}\n{stderr}".lower()
    # A not-logged-in signal (even on stdout with exit 0) is a sticky failure.
    if any(marker in both for marker in _NOT_LOGGED_IN_MARKERS):
        return "failed"
    if returncode == 0 and stdout.strip():
        return "verified"
    if any(marker in stderr.lower() for marker in _MODEL_UNAVAILABLE_MARKERS):
        return "failed"
    return "timeout"


def _verify_argv(provider: str, model: str) -> tuple[list[str], str | None] | None:
    """Minimal CLI invocation to test a model, or None if the provider takes no
    model (hermes/openclaw run their own configured model). Returns (argv, stdin)."""
    p = provider.lower()
    if p == "claude":
        return (["claude", "--print", "--model", model, "--tools", ""], "reply with ok")
    if p == "openai":
        return (
            ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
             "--model", model, "reply with ok"],
            None,
        )
    if p == "gemini":
        return (["gemini", "-p", "reply with ok", "-m", model, "--skip-trust"], None)
    return None


def _run_verifications(
    base: str, headers: dict[str, str], worklist: list[dict[str, str]]
) -> None:
    """Run each worklist model's test call (short timeout, off the turn executor)
    and best-effort POST the outcomes. Never raises into the poll loop."""
    results: list[dict[str, str | None]] = []
    for item in worklist:
        provider = str(item.get("provider") or "")
        model = str(item.get("model") or "")
        spec = _verify_argv(provider, model)
        if spec is None:
            continue
        argv, stdin = spec
        token = _call_timeout.set(float(_VERIFY_TIMEOUT))
        try:
            proc = _run(argv, stdin_input=stdin)
        except subprocess.TimeoutExpired:
            results.append(
                {"provider": provider, "model": model, "outcome": "timeout",
                 "error_text": "verification timed out"}
            )
            continue
        except OSError as exc:
            results.append(
                {"provider": provider, "model": model, "outcome": "timeout",
                 "error_text": f"could not run {provider} CLI: {exc}"}
            )
            continue
        finally:
            _call_timeout.reset(token)
        outcome = _classify_verify(proc.returncode, proc.stdout, proc.stderr, False)
        error_text = (
            None
            if outcome == "verified"
            else (proc.stderr.strip()[:300] or f"exit {proc.returncode}")
        )
        results.append(
            {"provider": provider, "model": model, "outcome": outcome,
             "error_text": error_text}
        )
    _post_verification_results(base, headers, results)


def _post_verification_results(
    base: str, headers: dict[str, str], results: list[dict[str, str | None]]
) -> None:
    """Best-effort POST of verification/play-time outcomes. Never raises."""
    if not results:
        return
    try:
        _http().post(
            f"{base}/api/agent/model-verification",
            headers=headers,
            json={"results": results},
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError as exc:
        print(
            f"[agentludum-connector] WARNING: could not report model verification: {exc}",
            file=sys.stderr,
        )


def _classify_play_failure(exc: BaseException) -> tuple[str, str]:
    """Classify a model failure that happened during a live turn into
    (outcome, reason). A timeout or unclassifiable error is retryable; a clear
    model-unavailable / not-logged-in signal is sticky failed."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout", "model call timed out during a live turn"
    text = str(exc)
    low = text.lower()
    if any(m in low for m in _NOT_LOGGED_IN_MARKERS) or any(
        m in low for m in _MODEL_UNAVAILABLE_MARKERS
    ):
        return "failed", text[:300]
    return "timeout", text[:300]


def _verify_tick(base: str, headers: dict[str, str]) -> None:
    """Pull the verification worklist and run it. Best-effort; never raises."""
    try:
        r = _http().get(f"{base}/api/agent/model-worklist", headers=headers, timeout=10)
        r.raise_for_status()
        # .json() must be inside the try: a 200 with a non-JSON body (CDN/proxy
        # HTML, route misconfig) raises ValueError, which would otherwise escape
        # and kill the poll loop.
        payload = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(
            f"[agentludum-connector] WARNING: could not fetch model worklist: {exc}",
            file=sys.stderr,
        )
        return
    worklist = payload.get("worklist") if isinstance(payload, dict) else None
    if isinstance(worklist, list):
        items = [item for item in worklist if isinstance(item, dict)]
        if items:
            _run_verifications(base, headers, items)


# ==============================================================================
# SECTION: Decision logic — resolve provider/model, get a move, build the
# submit request
# Picks the provider+model for a turn (`_resolve`), drives the adapter to get
# a move and salvages a missing HELP/HURT target with one re-ask (`_decide`),
# and shapes the move into the POST the server expects (`_move_request`).
# ==============================================================================


def _resolve(turn: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Pick the provider and model for a turn.

    Legacy payloads may still supply `preferred_provider` / `preferred_model`.
    New payloads supply `model` plus `agent_id` / `match_id`.
    """
    # Priority: --provider override > the server's explicit per-turn `provider`
    # field (the new routing sends it) > legacy preferred_provider > model
    # prefix. The explicit field means the connector no longer has to guess.
    payload_provider = (turn.get("provider") or "").lower()
    legacy_provider = (turn.get("preferred_provider") or "").lower()
    turn_provider = _provider_from_model(turn.get("model")) or _provider_from_model(
        turn.get("preferred_model")
    )
    provider = (
        args.provider
        or (payload_provider if payload_provider in _ADAPTERS else None)
        or (legacy_provider if legacy_provider in _ADAPTERS else None)
        or turn_provider
    )
    if provider not in _ADAPTERS:
        if (payload_provider or legacy_provider) and not args.provider:
            print(
                f"[agentludum-connector] turn is configured for "
                f"{(payload_provider or legacy_provider)!r}, which has no CLI runner — "
                f"using {DEFAULT_PROVIDER}. (MCP-only providers do not use this runner.)",
                file=sys.stderr,
            )
        provider = DEFAULT_PROVIDER
    adapter = _ADAPTERS[provider]
    if args.model:
        model = args.model
    elif args.provider:
        # A CLI --provider override forces a different CLI than the turn's; the
        # turn's model belongs to the agent's real provider and must not leak
        # into the overridden CLI — use this adapter's default model.
        model = adapter.default_model
    else:
        # Normal path: the server sends the agent's model in the payload; trust it.
        model = str(turn.get("model") or turn.get("preferred_model") or adapter.default_model)
    return provider, model


def _session_for_turn(
    turn: dict,
    args: argparse.Namespace,
    sessions: dict[tuple[str, str], _GameSession],
) -> _GameSession:
    key = _session_key(turn)
    sess = sessions.setdefault(key, _GameSession())
    provider, model = _resolve(turn, args)
    if sess.provider != provider or sess.model != model:
        sess.provider = provider
        sess.model = model
        sess.token = None
        sess.last_marker = (0, 0)
    return sess


def _poll_failed(consecutive: int) -> bool:
    """Return True when the circuit breaker should trip.

    Pure helper so tests can verify the threshold without running the poll loop.
    """
    return consecutive >= _POLL_FAIL_THRESHOLD


def _decide(turn: dict, sess: _GameSession) -> dict | None:
    """Get a move from this game's chained session; fall back to a default on any
    failure (and drop the session so the next turn re-establishes it).

    Returns ``None`` when this phase's deadline has already passed — the caller
    must NOT submit (it would only 410 and busy-loop); it should skip to the next
    live phase. On a model failure the returned move includes
    ``is_connector_fallback=True`` so the submission layer can mark the record.
    """
    adapter = _ADAPTERS[str(sess.provider)]
    history = turn.get("history", [])
    cur = turn["current"]
    phase = _phase(cur)
    match_id = _turn_match_id(turn)
    valid_ids = _valid_target_ids(turn)

    # Don't burn the whole phase thinking and then miss the deadline: bound the
    # model call to the time left. If the deadline has already passed, skip the
    # dead phase entirely (a submit would just 410); if there's a little time but
    # not enough to think, send a fallback so something still lands.
    budget = _phase_time_budget(cur)
    if budget is not None and budget <= 0:
        print(
            f"[agentludum-connector] {phase} deadline already passed "
            f"({budget:.0f}s) — skipping this dead phase (no submit).\n"
            f"  game={match_id} round={cur.get('round')} turn={cur.get('turn')}",
            file=sys.stderr,
        )
        return None
    if budget is not None and budget < _MIN_MODEL_SECONDS:
        default = _default_move(phase)
        print(
            f"[agentludum-connector] only {budget:.0f}s left before the "
            f"{phase} deadline — submitting FALLBACK move without calling the model.\n"
            f"  game={match_id} round={cur.get('round')} turn={cur.get('turn')}",
            file=sys.stderr,
        )
        return {**default, "is_connector_fallback": True}

    token = _call_timeout.set(budget)
    try:
        # A sessionless adapter (supports_resume=False, e.g. Hermes) gets the FULL
        # game state every turn — there is no chained session to send a delta to.
        if sess.token is None or not getattr(adapter, "supports_resume", True):
            text, usage = adapter.first(
                body=_setup_body(turn), framing=_framing(turn), model=str(sess.model), session=sess
            )
        else:
            new = [h for h in history if (h["round"], h["turn"]) > sess.last_marker]
            text, usage = adapter.resume(
                body=_delta_body(new, turn.get("scoreboard", []), cur),
                model=str(sess.model),
                session=sess,
            )
        decision = _normalize_move(_parse_move(text), phase)
        # An ACT HELP/HURT must name a valid target. If the model dropped it, the
        # server rejects the move (400); the poll loop would then re-serve the turn
        # and we'd re-submit the same doomed move until the deadline and default to
        # HOARD (a "missed turn"). Salvage it: re-ask the model ONCE for a valid
        # target (bounded by the time left); if that still fails, HOARD — a valid
        # move that CLOSES the turn — rather than storming rejected moves.
        if phase == "act" and decision["action"] in ("HELP", "HURT"):
            if not _target_is_valid(decision.get("target_id"), valid_ids):
                remaining = _phase_time_budget(cur)
                retried: dict | None = None
                if (
                    getattr(adapter, "supports_resume", True)
                    and sess.token is not None
                    and (remaining is None or remaining >= _MIN_MODEL_SECONDS)
                ):
                    if remaining is not None:
                        _call_timeout.set(remaining)  # bound the re-ask to time left
                    retry_text, retry_usage = adapter.resume(
                        body=_retarget_body(decision["action"], valid_ids, cur),
                        model=str(sess.model),
                        session=sess,
                    )
                    usage = _sum_usage(usage, retry_usage)
                    candidate = _normalize_move(_parse_move(retry_text), phase)
                    if candidate["action"] in ("HELP", "HURT") and _target_is_valid(
                        candidate.get("target_id"), valid_ids
                    ):
                        retried = candidate
                if retried is not None:
                    decision = retried
                    print(
                        f"[agentludum-connector] {match_id} R{cur.get('round')}T{cur.get('turn')} "
                        f"recovered a valid {decision['action']} target on re-ask.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[agentludum-connector] {match_id} R{cur.get('round')}T{cur.get('turn')} "
                        f"{decision['action']} had no valid target after a re-ask — HOARDing "
                        f"to avoid a rejected-move retry storm.",
                        file=sys.stderr,
                    )
                    decision = {
                        "action": "HOARD",
                        "target_id": None,
                        "thinking": decision.get("thinking", ""),
                        "is_connector_fallback": True,
                    }
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        default = _default_move(phase)
        print(
            f"[agentludum-connector] ERROR: LLM subprocess failed — submitting FALLBACK move.\n"
            f"  game={match_id} round={cur.get('round')} turn={cur.get('turn')} phase={phase}\n"
            f"  provider={sess.provider} failure={exc!r}\n"
            f"  fallback_move={default}",
            file=sys.stderr,
        )
        sess.token = None  # a bad resume → re-establish the session next turn
        # Surface WHY this turn failed: a real model-subprocess failure flips the
        # model's verification status (fail-loud). The marker rides the move dict
        # (ignored by _move_request) and _handle_turn POSTs it up-channel. The
        # deadline-passed and too-little-time branches above never reach here, so
        # they are never mis-reported as model failures.
        outcome, reason = _classify_play_failure(exc)
        return {
            **default,
            "is_connector_fallback": True,
            "model_failure": {
                "provider": str(sess.provider),
                "model": str(sess.model),
                "outcome": outcome,
                "error_text": reason,
            },
        }
    finally:
        _call_timeout.reset(token)
    if usage:
        _record_usage(match_id, cur, usage, sess)
    if history:
        sess.last_marker = max((h["round"], h["turn"]) for h in history)
    return decision


def _move_request(
    base: str, match_id: str, turn: dict, decision: dict
) -> tuple[str, dict, dict]:
    """Build (url, query_params, json_body) for POSTing this turn's move.

    The query params carry ``agent_turn_token``, which the server requires to
    bind the move to this agent+turn — omitting it 422s every submission and the
    agent silently defaults every turn.
    """
    cur = turn["current"]
    params = {"agent_turn_token": turn["agent_turn_token"]}
    is_fallback = bool(decision.get("is_connector_fallback"))
    if _phase(cur) == "talk":
        return (
            f"{base}/api/games/{match_id}/message",
            params,
            {
                "turn_token": cur["turn_token"],
                "message": _clip(decision.get("message", ""), _MESSAGE_MAX_LENGTH),
                "thinking": _clip(decision.get("thinking", ""), _THINKING_MAX_LENGTH),
                "is_connector_fallback": is_fallback,
            },
        )
    return (
        f"{base}/api/games/{match_id}/submit",
        params,
        {
            "turn_token": cur["turn_token"],
            "action": str(decision.get("action", "HOARD")).upper(),
            "target_id": decision.get("target_id") or None,
            "thinking": _clip(decision.get("thinking", ""), _THINKING_MAX_LENGTH),
            "is_connector_fallback": is_fallback,
        },
    )


# ==============================================================================
# SECTION: OS service install — one command per platform
# Builds a login-persistent background service (macOS launchd plist, Linux
# systemd user unit, or a Windows scheduled task) so the operator (or their AI
# assistant) never has to hand-roll the OS-specific daemonizing.
# ==============================================================================

_SERVICE_LABEL = "com.agentludum.connector"
_LINUX_UNIT_NAME = "agentludum-connector.service"
_WINDOWS_TASK_NAME = "AgentLudumConnector"


@dataclass
class _InstallPlan:
    """A platform-agnostic install recipe: files to write, then commands to run.

    Pure data so it can be asserted in tests without touching the real system.
    Each command is (argv, allow_fail); allow_fail=True swallows a non-zero exit
    (e.g. unloading a service that isn't loaded yet, on an idempotent re-install).
    """

    files: list[tuple[str, str, int]]  # (path, content, chmod-mode)
    commands: list[tuple[list[str], bool]]  # (argv, allow_fail)
    note: str = ""


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Dirs where user-installed CLIs commonly live but a service PATH usually omits.
# Baked into the service as a safety net even if the installing shell's PATH is
# unusual.
_FALLBACK_PATH_DIRS = (
    f"{Path.home()}/.local/bin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
)
# CLIs the connector shells out to. Their install dir must be on the service
# PATH or the connector can neither detect nor drive them. `node` is included
# because the JS-based CLIs (gemini, codex) need it on PATH at runtime.
_RUNTIME_BINARIES = ("claude", "codex", "gemini", "node", "hermes", "openclaw")


def _install_path() -> str:
    """Build the PATH to bake into the installed service.

    launchd / systemd start a service with a minimal PATH that omits the dirs
    where user-installed CLIs live (``~/.local/bin``, ``/opt/homebrew/bin``, …).
    The connector finds and runs those CLIs by name, so without their dirs on
    PATH every provider shows "CLI not found" and turns fall back to HOARD. We
    bake in the installing shell's PATH (which has them), the dir of every CLI
    we can resolve right now, and a few common fallbacks — first occurrence wins.
    """
    dirs: list[str] = []
    seen: set[str] = set()

    def _add(directory: str) -> None:
        if directory and directory not in seen:
            seen.add(directory)
            dirs.append(directory)

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        _add(directory)
    for binary in _RUNTIME_BINARIES:
        resolved = shutil.which(binary)
        if resolved:
            _add(str(Path(resolved).parent))
    for directory in _FALLBACK_PATH_DIRS:
        _add(directory)
    return os.pathsep.join(dirs)


def _macos_install_plan(
    python_exe: str, script_path: str, key: str, url: str, home: str, uid: int, path: str
) -> _InstallPlan:
    plist_path = f"{home}/Library/LaunchAgents/{_SERVICE_LABEL}.plist"
    log_dir = f"{home}/.agentludum"
    program_args = [python_exe, script_path, "--key", key, "--url", url]
    args_xml = "".join(f"        <string>{_xml_escape(a)}</string>\n" for a in program_args)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"    <key>Label</key><string>{_SERVICE_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{args_xml}"
        "    </array>\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        f"        <key>PATH</key><string>{_xml_escape(path)}</string>\n"
        "    </dict>\n"
        "    <key>RunAtLoad</key><true/>\n"
        "    <key>KeepAlive</key><true/>\n"
        f"    <key>StandardOutPath</key><string>{log_dir}/connector.log</string>\n"
        f"    <key>StandardErrorPath</key><string>{log_dir}/connector.err</string>\n"
        "</dict>\n"
        "</plist>\n"
    )
    domain = f"gui/{uid}"
    commands: list[tuple[list[str], bool]] = [
        # Clear quarantine/provenance xattrs a network download picks up, which
        # otherwise make launchd refuse the file. Best-effort.
        (["xattr", "-c", script_path], True),
        # Re-enable the label in case a prior run left it disabled. Idempotent.
        (["launchctl", "enable", f"{domain}/{_SERVICE_LABEL}"], True),
        # Unload any previous copy first so re-installing is clean. Ok to fail
        # when nothing is loaded yet.
        (["launchctl", "bootout", domain, plist_path], True),
        # The one that must succeed: load + start the service.
        (["launchctl", "bootstrap", domain, plist_path], False),
    ]
    return _InstallPlan(files=[(plist_path, plist, 0o600)], commands=commands)


def _linux_install_plan(
    python_exe: str, script_path: str, key: str, url: str, home: str, path: str
) -> _InstallPlan:
    unit_path = f"{home}/.config/systemd/user/{_LINUX_UNIT_NAME}"
    unit = (
        "[Unit]\n"
        "Description=Agent Ludum connector\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"Environment=PATH={path}\n"
        f"ExecStart={python_exe} {script_path} --key {key} --url {url}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    commands: list[tuple[list[str], bool]] = [
        (["systemctl", "--user", "daemon-reload"], False),
        (["systemctl", "--user", "enable", "--now", _LINUX_UNIT_NAME], False),
    ]
    return _InstallPlan(files=[(unit_path, unit, 0o600)], commands=commands)


def _windows_install_plan(
    python_exe: str, script_path: str, key: str, url: str
) -> _InstallPlan:
    run = f'"{python_exe}" "{script_path}" --key {key} --url {url}'
    commands: list[tuple[list[str], bool]] = [
        (
            ["schtasks", "/create", "/tn", _WINDOWS_TASK_NAME, "/tr", run,
             "/sc", "onlogon", "/rl", "limited", "/f"],
            False,
        ),
    ]
    note = (
        "Note: Windows Task Scheduler does not auto-restart an on-logon task if it "
        "stops. If the connector dies, log back in or re-run it to restart."
    )
    return _InstallPlan(files=[], commands=commands, note=note)


def _run_install_plan(plan: _InstallPlan) -> None:
    """Write the plan's files (secure perms) then run its commands in order."""
    for path, content, mode in plan.files:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, mode)
    for argv, allow_fail in plan.commands:
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0 and not allow_fail:
            raise RuntimeError(
                f"`{' '.join(argv)}` failed (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )


def _install_service(key: str, url: str) -> int:
    """Install the connector as a login-persistent background service, then exit.

    One command per OS — the AI assistant runs this instead of improvising the
    most error-prone, OS-specific part (daemonizing + macOS security xattrs).
    """
    system = platform.system()
    python_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    home = str(Path.home())
    path = _install_path()
    if system == "Darwin":
        plan = _macos_install_plan(python_exe, script_path, key, url, home, os.getuid(), path)
    elif system == "Linux":
        plan = _linux_install_plan(python_exe, script_path, key, url, home, path)
    elif system == "Windows":
        plan = _windows_install_plan(python_exe, script_path, key, url)
    else:
        print(
            f"[agentludum-connector] --install is not supported on {system!r}. Run the "
            f"connector directly instead: python3 {script_path} --key {key} --url {url}",
            file=sys.stderr,
        )
        return 1
    try:
        _run_install_plan(plan)
    except (RuntimeError, OSError) as exc:
        print(f"[agentludum-connector] install failed: {exc}", file=sys.stderr)
        return 1
    print(
        "[agentludum-connector] installed as a background service that starts on "
        "login and restarts if it stops. It is running now."
    )
    if plan.note:
        print(plan.note)
    return 0


# ==============================================================================
# SECTION: Singleton lock — one connector per connection key per machine
# A POSIX flock keyed by the connection key, so a stray second copy (e.g. a
# foreground run plus the installed service) can't double-poll and double-play.
# ==============================================================================


def _acquire_singleton_lock(key: str):
    """Stop a second connector with the SAME key from running on this machine.

    Prevents the accidental "foreground copy + service copy" duplicate. Keyed by
    the connection key so distinct connections can still run side by side. POSIX
    only (flock); a no-op elsewhere. Returns the held handle (keep it alive) or
    None. Exits 0 if another instance already holds the lock.
    """
    if os.name != "posix":
        return None
    import fcntl

    lock_dir = Path.home() / ".agentludum"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = open(lock_dir / f"connector-{key[-8:]}.lock", "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            "[agentludum-connector] another connector is already running for this "
            "key on this machine; exiting.",
            file=sys.stderr,
        )
        sys.exit(0)
    return handle


def _ts() -> str:
    """Short wall-clock stamp for diagnostic timing logs."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# ==============================================================================
# SECTION: Turn execution — decide, submit, and report one turn
# Runs in its own worker thread per (agent_id, match_id) session: primes or
# deltas the session, calls `_decide`, POSTs the move, and logs timing. A
# crash here can't wedge the poll loop — see `_make_release_cb`.
# ==============================================================================


def _handle_turn(
    base: str, headers: dict[str, str], turn: dict, sess: _GameSession
) -> None:
    """Decide and submit one turn end-to-end. Runs in a worker thread, so it owns
    all its own I/O and swallows its own POST errors. A given session is handed to
    exactly one worker at a time (the caller's in-flight guard), so reading and
    mutating ``sess`` here is race-free."""
    match_id = _turn_match_id(turn)
    cur = turn["current"]
    phase = _phase(cur)
    # Diagnostic timing: how much of the phase deadline was left when this worker
    # picked up the turn (t0), so we can tell "server served it late" (little left
    # on arrival) from "connector was slow to submit" (plenty on arrival, late POST).
    t0 = time.monotonic()
    arrival = _phase_time_budget(cur)
    arrival_str = f"{arrival:.0f}s" if arrival is not None else "?"
    print(
        f"[agentludum-connector] {_ts()} {match_id} R{cur['round']}T{cur['turn']} "
        f"{phase.upper()} received — {arrival_str} left on arrival",
        file=sys.stderr,
    )
    adapter = _ADAPTERS[str(sess.provider)]
    # A session that is about to be PRIMED (a brand-new chained session, or a
    # sessionless provider that re-primes every turn) gets the full game so far,
    # not the small rolling window the poll payload carries. Pull it once here so
    # the model opens with the whole match in view; a continuing session reads its
    # delta straight from the windowed payload and skips the extra request.
    if sess.token is None or not getattr(adapter, "supports_resume", True):
        full = _fetch_full_history(base, match_id)
        if full is not None:
            history, scoreboard = full
            turn = {**turn, "history": history, "scoreboard": scoreboard}
    if sess.token is None:
        agent_name = turn.get("agent_name", turn.get("agent_id", "unknown"))
        version_no = turn.get("version_no", "?")
        print(
            f"[agentludum-connector] {match_id}: agent {agent_name} "
            f"(agent {turn.get('agent_id', 'unknown')}, v{version_no}) on {sess.provider} ({sess.model})."
        )
    decision = _decide(turn, sess)
    if decision is None:
        # This phase's deadline already passed — let it resolve server-side; the
        # next poll hands us the next live phase. No submit (it would just 410).
        print(
            f"[agentludum-connector] {_ts()} {match_id} R{cur['round']}T{cur['turn']} "
            f"{phase.upper()} SKIPPED (deadline passed) — {time.monotonic() - t0:.1f}s "
            f"after this worker picked it up",
            file=sys.stderr,
        )
        return
    failure = decision.get("model_failure")
    is_fallback = bool(decision.get("is_connector_fallback"))
    url, params, body = _move_request(base, match_id, turn, decision)
    try:
        r2 = _http().post(url, headers=headers, params=params, json=body, timeout=20)
    except httpx.HTTPError as exc:
        print(
            f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} "
            f"{phase.upper()} POST failed: {exc}",
            file=sys.stderr,
        )
        return
    fallback_tag = " [FALLBACK]" if is_fallback else ""
    took = f"handled in {time.monotonic() - t0:.1f}s"
    if phase == "talk":
        print(
            f"[agentludum-connector] {_ts()} {match_id} R{cur['round']}T{cur['turn']} TALK: "
            f"({r2.status_code}){fallback_tag} — {took}"
        )
    else:
        target = body.get("target_id")
        arrow = f" -> {target}" if target else ""
        print(
            f"[agentludum-connector] {_ts()} {match_id} R{cur['round']}T{cur['turn']} ACT: "
            f"{body['action']}{arrow} ({r2.status_code}){fallback_tag} — {took}"
        )
    # Fail-loud: a real play-time model failure flips the model's verification
    # status up-channel so it surfaces on status (best-effort, after the move).
    if failure:
        _post_verification_results(base, headers, [failure])


def _make_release_cb(
    key: tuple[str, str], in_flight: set[tuple[str, str]], lock: threading.Lock
) -> Callable[[Future[None]], None]:
    """Build a done-callback that frees a session's in-flight slot and surfaces a
    worker crash. A crash must not silently wedge the session forever."""

    def _release(fut: Future[None]) -> None:
        with lock:
            in_flight.discard(key)
        exc = fut.exception()
        if exc is not None:
            print(
                f"[agentludum-connector] worker for session {key} crashed: {exc!r}",
                file=sys.stderr,
            )

    return _release


# ==============================================================================
# SECTION: Poll loop & entry point
# `main()` parses args, optionally installs the service, takes the singleton
# lock, then loops forever: poll for servable turns, fan them out to worker
# threads (one per session), and run readiness verification while idle.
# ==============================================================================


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Chained connector runner for Hoard-Hurt-Help (auto-selects the agent provider)"
    )
    ap.add_argument("--key", required=True, help="Your connection key (sk_conn_...)")
    ap.add_argument("--url", default=DEFAULT_URL, help="Game server base URL")
    ap.add_argument(
        "--provider", choices=sorted(_ADAPTERS), default=None,
        help="Override the provider inferred from the turn payload (claude/openai/gemini).",
    )
    ap.add_argument(
        "--model", default=None, help="Override the model from the turn payload.",
    )
    ap.add_argument(
        "--install", action="store_true",
        help="Install as a background service that starts on login, then exit.",
    )
    args = ap.parse_args()

    # Flush logs immediately. Under launchd, block-buffered stdout froze
    # connector.log for hours, hiding what the connector was doing; line-buffering
    # both streams keeps output (and the per-turn timing below) actually visible.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)

    if args.install:
        sys.exit(_install_service(args.key, args.url))

    # Hold a per-key singleton lock for the whole run so a stray second copy
    # can't double-poll. Kept in a local so the handle (and lock) outlive setup.
    _singleton_lock = _acquire_singleton_lock(args.key)

    base = args.url.rstrip("/")
    headers = {"X-Connection-Key": args.key}
    sessions: dict[tuple[str, str], _GameSession] = {}
    pid = os.getpid()
    _report_pid(base, headers, pid)
    last_detect_report = time.monotonic()
    last_verify = 0.0  # 0 → verify on the first idle poll
    print(
        f"[agentludum-connector] connected to {base}; PID {pid}; one chained session per agent+match."
    )

    # Circuit-breaker state: count consecutive failed polls. After
    # _POLL_FAIL_THRESHOLD consecutive failures we give up and exit so the
    # process doesn't spin forever against a dead server.
    consecutive_poll_failures = 0

    # Concurrency state: each servable turn runs in its own worker so a slow model
    # call in one match never blocks another. `in_flight` (guarded by state_lock)
    # tracks which agent+match sessions a worker already owns, so a re-poll that
    # returns the same still-open turn doesn't double-dispatch it. `use_plural`
    # flips off if the server is too old to expose the batch endpoint.
    executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY)
    in_flight: set[tuple[str, str]] = set()
    state_lock = threading.Lock()
    use_plural = True
    # Set after a 401/410 so we log that outage once, not on every poll while we
    # idle waiting for a reinstall.
    auth_blocked = False
    # Model verification runs on its OWN single worker, off the poll thread, so a
    # slow CLI test call can never stall polling (and so never delays a live turn
    # the way an inline verify could). One verify at a time, guarded by
    # `verify_running`.
    verify_executor = ThreadPoolExecutor(max_workers=1)
    verify_running = threading.Event()

    def _verify_release(fut: Future[None]) -> None:
        verify_running.clear()
        exc = fut.exception()
        if exc is not None:
            print(
                f"[agentludum-connector] verification worker crashed: {exc!r}",
                file=sys.stderr,
            )

    while True:
        # Refresh detection periodically so a CLI installed (or a provider
        # turned on) after startup shows as "detected" without a restart.
        if time.monotonic() - last_detect_report >= _DETECT_REPORT_INTERVAL:
            _report_pid(base, headers, pid)
            last_detect_report = time.monotonic()
        try:
            endpoint = "next-turns" if use_plural else "next-turn"
            r = _http().get(f"{base}/api/agent/{endpoint}", headers=headers, timeout=40)
        except httpx.HTTPError as exc:
            consecutive_poll_failures += 1
            print(
                f"[agentludum-connector] network error: {exc}; retrying in 5s "
                f"(consecutive failures: {consecutive_poll_failures}/{_POLL_FAIL_THRESHOLD})",
                file=sys.stderr,
            )
            if _poll_failed(consecutive_poll_failures):
                print(
                    f"[agentludum-connector] FATAL: {consecutive_poll_failures} consecutive poll "
                    f"failures — server appears permanently unreachable. Exiting.",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(5)
            continue

        if r.status_code in (401, 410):
            # Connection deleted (410) or key revoked/invalid (401). Do NOT exit:
            # under launchd/systemd auto-restart, exiting just respawns us to hit
            # the same status within seconds — a churning, log-spamming spin that
            # never makes progress. Idle slowly and keep checking instead; a
            # reinstall replaces this process via bootout, and a manual foreground
            # run can be Ctrl-C'd. Log once per outage, not on every poll.
            if not auth_blocked:
                auth_blocked = True
                reason = (
                    "connection deleted"
                    if r.status_code == 410
                    else "connection key revoked or invalid (401)"
                )
                print(
                    f"[agentludum-connector] {reason}; idling. This runner keeps "
                    "checking and is replaced automatically when you reinstall.",
                    file=sys.stderr,
                )
            consecutive_poll_failures = 0
            time.sleep(_AUTH_BLOCKED_SLEEP_SECONDS)
            continue
        auth_blocked = False  # any non-401/410 response means the key works again
        if r.status_code == 403:  # connection paused by its owner
            consecutive_poll_failures = 0
            time.sleep(30)
            continue
        if r.status_code == 429:  # polled too fast
            consecutive_poll_failures = 0
            time.sleep(1)
            continue
        if r.status_code == 404 and use_plural:
            # Server predates the batch endpoint; degrade to single-turn polling
            # (serial play) rather than crash-looping on a missing route.
            use_plural = False
            print(
                "[agentludum-connector] server has no /next-turns endpoint; falling "
                "back to single-turn polling (update the server for concurrent play).",
                file=sys.stderr,
            )
            consecutive_poll_failures = 0
            continue
        if r.status_code != 200:
            consecutive_poll_failures += 1
            print(
                f"[agentludum-connector] {r.status_code}: {r.text[:200]}; retrying "
                f"(consecutive failures: {consecutive_poll_failures}/{_POLL_FAIL_THRESHOLD})",
                file=sys.stderr,
            )
            if _poll_failed(consecutive_poll_failures):
                print(
                    f"[agentludum-connector] FATAL: {consecutive_poll_failures} consecutive poll "
                    f"failures — server appears permanently unreachable. Exiting.",
                    file=sys.stderr,
                )
                sys.exit(1)
            time.sleep(5)
            continue

        # Successful poll — reset the circuit breaker.
        consecutive_poll_failures = 0

        data = r.json()
        if data.get("status") != "your_turn":
            # Idle: a good moment to fail-fast-verify models (never during a live
            # turn, so play is never delayed). Cap the nap so the ~60s cadence is
            # real even when the server asks us to wait longer.
            if _should_verify(time.monotonic(), last_verify) and not verify_running.is_set():
                last_verify = time.monotonic()
                verify_running.set()
                verify_executor.submit(_verify_tick, base, headers).add_done_callback(
                    _verify_release
                )
            time.sleep(min(data.get("next_poll_after_seconds", 5), _VERIFY_INTERVAL))
            continue

        # Plural endpoint returns every servable turn; the legacy singular endpoint
        # IS the turn. Either way we get a list to fan out to the worker pool.
        turns = data.get("turns", []) if use_plural else [data]
        for turn in turns:
            try:
                key = _session_key(turn)
            except ValueError:
                # A turn payload missing agent_id/match_id can't be routed to a
                # session; skip it rather than crash the whole poll loop.
                continue
            with state_lock:
                if key in in_flight:
                    # A worker is already on this session — don't double-dispatch.
                    continue
                in_flight.add(key)
                sess = _session_for_turn(turn, args, sessions)
            fut = executor.submit(_handle_turn, base, headers, turn, sess)
            fut.add_done_callback(_make_release_cb(key, in_flight, state_lock))

        # Re-poll on a short cadence so newly-opened phases (e.g. ACT after TALK)
        # dispatch promptly without busy-looping the server.
        time.sleep(_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
