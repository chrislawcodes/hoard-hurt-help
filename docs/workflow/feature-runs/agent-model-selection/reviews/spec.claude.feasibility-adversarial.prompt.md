Review this spec artifact using a feasibility-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
Code context files are provided above. Before asserting any finding, check whether it is confirmed or refuted by the provided code. Each finding must include an evidence tag:
  [CODE-CONFIRMED] — the code directly supports this finding
  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)
  [UNVERIFIED] — relevant code was not provided; treat as lower confidence
Only assign HIGH severity to CODE-CONFIRMED findings.
The full review artifact text is included below in this prompt.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.

Context: agentludum_connector.py
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

# Circuit-breaker threshold for the poll loop. Each failed poll sleeps ~5 s, so
# 24 consecutive failures ≈ 2 minutes of a permanently unreachable server before
# we give up and exit with a non-zero code.
_POLL_FAIL_THRESHOLD = 24

# How often the connector re-reports its PID + detected provider CLIs while
# running. Detection is otherwise a one-shot at startup, so installing a CLI (or
# turning a provider on) would not show as "detected" until the next restart.
# Re-reporting on this interval lets the website reflect a freshly installed CLI
# within a few minutes without the operator restarting the connector.
_DETECT_REPORT_INTERVAL = 300  # seconds

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


# --------------------------------------------------------------------------
# Shared helpers (identical across every provider)
# --------------------------------------------------------------------------


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
    return (
        f"ACT PHASE — here are this turn's messages: {_format_talk_messages(cur)} "
        f"— JSON only.{clock}"
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
        resp = httpx.get(f"{base}/api/spectator/games/{match_id}/state", timeout=20)
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


# --------------------------------------------------------------------------
# Provider adapters — the only per-provider code
# --------------------------------------------------------------------------


class _ClaudeAdapter:
    """`claude --print` with `--system-prompt` for framing and `--resume` for the
    chained session. Captures the session id Claude hands back. Reports usage."""

    cli = "claude"
    default_model = "claude-haiku-4-5"

    def _call(self, argv: list[str], body: str) -> dict:
        proc = _run(argv, stdin_input=body)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"claude exit {proc.returncode}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude returned non-JSON: {proc.stdout[:300]}") from exc

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


# --------------------------------------------------------------------------
# Provider resolution + the decision
# --------------------------------------------------------------------------


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
        httpx.post(
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
        move = _parse_move(text)
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
        return {**default, "is_connector_fallback": True}
    finally:
        _call_timeout.reset(token)
    if usage:
        _record_usage(match_id, cur, usage, sess)
    if history:
        sess.last_marker = max((h["round"], h["turn"]) for h in history)
    return _normalize_move(move, phase)


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


# --------------------------------------------------------------------------
# Service install — one command sets up the persistent background service, so
# the AI assistant never has to hand-roll launchd / systemd / Task Scheduler.
# --------------------------------------------------------------------------

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
        return
    is_fallback = bool(decision.get("is_connector_fallback"))
    url, params, body = _move_request(base, match_id, turn, decision)
    try:
        r2 = httpx.post(url, headers=headers, params=params, json=body, timeout=20)
    except httpx.HTTPError as exc:
        print(
            f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} "
            f"{phase.upper()} POST failed: {exc}",
            file=sys.stderr,
        )
        return
    fallback_tag = " [FALLBACK]" if is_fallback else ""
    if phase == "talk":
        print(
            f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} TALK: "
            f"({r2.status_code}){fallback_tag}"
        )
    else:
        target = body.get("target_id")
        arrow = f" -> {target}" if target else ""
        print(
            f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} ACT: "
            f"{body['action']}{arrow} ({r2.status_code}){fallback_tag}"
        )


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

    while True:
        # Refresh detection periodically so a CLI installed (or a provider
        # turned on) after startup shows as "detected" without a restart.
        if time.monotonic() - last_detect_report >= _DETECT_REPORT_INTERVAL:
            _report_pid(base, headers, pid)
            last_detect_report = time.monotonic()
        try:
            endpoint = "next-turns" if use_plural else "next-turn"
            r = httpx.get(f"{base}/api/agent/{endpoint}", headers=headers, timeout=40)
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
            if r.status_code == 410:
                message = (
                    "[agentludum-connector] connection deleted; exiting now so the "
                    "replacement can start."
                )
            else:
                message = (
                    "[agentludum-connector] connection deleted or key revoked (401). "
                    "Stop this runner and install the replacement."
                )
            print(
                message,
                file=sys.stderr,
            )
            return
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
            time.sleep(data.get("next_poll_after_seconds", 5))
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


Context: config.py
"""Settings loaded from environment variables.

Single source of truth for runtime config. Other modules import
`settings` from here; nothing else should touch `os.environ`.
"""

import logging
import os
from functools import lru_cache

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


def _parse_email_set(raw: str) -> set[str]:
    """Split a comma-separated email list into a normalized lowercased set."""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Public-facing base URL of this deployment. Drives setup commands shown
    # to players, the OAuth redirect, the MCP server URL, etc.
    base_url: str = Field(default="http://localhost:8000")

    # Database connection. SQLite for dev, Postgres on Railway.
    database_url: str = Field(default="sqlite+aiosqlite:///./hoardhurthelp.db")

    # Google OAuth client. Required for sign-in.
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_redirect_uri: str = Field(default="http://localhost:8000/auth/google/callback")

    # MCP OAuth bridge. The server signs its own client tokens and registers
    # one or more redirect URIs with Google.
    mcp_jwt_signing_key: str = Field(default="")
    mcp_redirect_uris: str = Field(default="")

    # Signing key for session cookies. Generate with `secrets.token_hex(32)`.
    session_secret: str = Field(default="dev-only-do-not-use-in-prod-" + "x" * 40)

    # Mark the session cookie Secure (HTTPS-only). Set true in production behind
    # HTTPS; leave false for local http dev.
    cookie_secure: bool = Field(default=False)

    # --- Admin role split ---
    # Platform admin: game catalog, user handles, incidents.
    platform_admin_emails: str = Field(default="")
    # Game admin: per-game match creation, strategy prompts, export.
    # Set GAME_ADMIN_EMAILS__HOARD_HURT_HELP=alice@example.com for each game.
    # (Populated at construction time via _collect_game_admin_emails below.)
    user_active_match_limit: int = Field(default=3)

    # Compatibility: legacy single-role admin list. Kept as fallback while
    # PLATFORM_ADMIN_EMAILS / GAME_ADMIN_EMAILS__* are being rolled out.
    # Remove this field once all prod env vars are updated.
    admin_emails: str = Field(default="")

    # Internal storage populated by _collect_game_admin_emails validator; not an env var.
    _game_admin_emails_raw: dict[str, str] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _collect_game_admin_emails(self) -> "Settings":
        """Scan os.environ for GAME_ADMIN_EMAILS__* and populate _game_admin_emails_raw."""
        prefix = "GAME_ADMIN_EMAILS__"
        result: dict[str, str] = {}
        for k, v in os.environ.items():
            if k.upper().startswith(prefix):
                result[k[len(prefix):].upper()] = v  # e.g. "HOARD_HURT_HELP" → value
        self._game_admin_emails_raw = result
        return self

    @field_validator("database_url")
    @classmethod
    def _force_async_driver(cls, v: str) -> str:
        """Normalize a sync Postgres URL to the asyncpg driver.

        Railway's Postgres add-on hands out a sync URL (``postgres://`` or
        ``postgresql://``), but our engine uses ``create_async_engine`` and
        needs the asyncpg driver. Rewriting here lets a deploy paste Railway's
        ``${{Postgres.DATABASE_URL}}`` value verbatim. SQLite and an already
        async URL pass through untouched. Alembic re-strips the suffix for its
        own sync run in migrations/env.py.
        """
        if v.startswith("postgresql+asyncpg://"):
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def admin_emails_set(self) -> set[str]:
        """Normalized lowercased set of admin emails (legacy; prefer platform_admin_emails_set)."""
        return _parse_email_set(self.admin_emails)

    @property
    def platform_admin_emails_set(self) -> set[str]:
        """Platform admins. Falls back to admin_emails during compat window."""
        raw = self.platform_admin_emails or self.admin_emails
        if not raw:
            return set()
        if self.platform_admin_emails == "" and self.admin_emails:
            _log.warning(
                "ADMIN_EMAILS fallback active — set PLATFORM_ADMIN_EMAILS to remove"
            )
        return _parse_email_set(raw)

    def game_admin_emails_for(self, game: str) -> set[str]:
        """Return the game-admin email set for a slug like 'hoard-hurt-help'.

        Normalizes slug → uppercase with underscores to look up the env var suffix.
        Falls back to admin_emails during the compat window.
        """
        key = game.upper().replace("-", "_")
        raw = self._game_admin_emails_raw.get(key, "")
        if not raw and self.admin_emails:
            _log.warning(
                "ADMIN_EMAILS fallback active for game %s — set GAME_ADMIN_EMAILS__%s",
                game,
                key,
            )
            raw = self.admin_emails
        if not raw:
            return set()
        return _parse_email_set(raw)

    @property
    def all_game_admin_emails_set(self) -> set[str]:
        """Union of all game-admin emails across every configured game."""
        result: set[str] = set()
        for raw in self._game_admin_emails_raw.values():
            result.update(_parse_email_set(raw))
        return result


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


settings = get_settings()

PROVIDER_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-8",
    ],
    "gemini": [
        "gemini-3.1-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
    ],
    "openai": [
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-5.5",
    ],
    "hermes": [],
    "openclaw": [],
}


def _assert_unique_non_empty_provider_models(provider_models: dict[str, list[str]]) -> None:
    """Ensure the non-empty provider allowlists do not share a model name."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for provider, models in provider_models.items():
        if not models:
            continue
        for model in models:
            prior = seen.get(model)
            if prior is not None and prior != provider:
                duplicates.append(f"{model!r} in {prior} and {provider}")
            else:
                seen[model] = provider
    if duplicates:
        raise AssertionError(
            "Duplicate model names across non-empty provider allowlists: "
            + ", ".join(sorted(duplicates))
        )


_assert_unique_non_empty_provider_models(PROVIDER_MODELS)


def provider_for_model(model: str) -> str | None:
    """Reverse-map a model name to its provider via PROVIDER_MODELS.

    The single source of truth for model→provider (the assertion above keeps
    model names unique across the non-empty allowlists, so this is
    unambiguous). Returns None for a model in no allowlist — e.g. a freeform
    Hermes/OpenClaw model whose provider must come from elsewhere (the stored
    `agents.provider`), not from the model name.
    """
    for provider, models in PROVIDER_MODELS.items():
        if model in models:
            return provider
    return None


Artifact: spec.md
# Spec: Per-agent model selection with fail-fast verification

## Status — slice 1 shipped; this run builds slices 2–4

Slice 1 (the backend foundation) is **already merged to main** (#572): `Agent.preferred_model` (migration 0044) + the server-side three-layer model resolution (`resolve_seat_model`, removing legacy `version.model`). FR-001, FR-002, FR-003, FR-004, FR-008 (resolution half), FR-011 and the empty-allowlist rule are satisfied by it.

**This Feature Factory run implements the remaining slices on top of current main:**
- **Slice 2 — Connector model verification** (FR-005, FR-006, FR-007, FR-013, FR-015, FR-016): the dedicated down/up verification channels + the connector's cheap test call + the server cache + the per-model UI status.
- **Slice 3 — Fail-loud at play time** (FR-009, FR-009a, FR-018): route the play-time failure reason on the up-channel and surface it; classify transient vs real.
- **Slice 4 — Agent-settings UI** (FR-001 UI control, FR-010, FR-012, FR-014, FR-017): the preferred-model picker (advanced), the effective-model display, and the join-time warning.

The plan and tasks below cover only slices 2–4. Slice 1's requirements remain in this spec for context but are done.

## Background

Agents are decoupled from any AI model/provider (PR #470): an agent is just a name + a strategy, and `AgentVersion.model` is legacy/NULL. PR #569 added a payload guard (`app/engine/model_provider_match.py:model_for_provider`) so a seat never runs a model belonging to a different provider, backfilled away leftover legacy models (migration 0043), and fixed a `codex exec resume` connector bug.

Today the model a seat runs is decided implicitly:

- **Machine connection** (the always-on connector daemon, `scripts/agentludum_connector.py`): the connector picks a hardcoded per-provider default (`claude-haiku-4-5`, `gpt-5.4-mini`, `gemini-3-flash-preview` — `_resolve`/adapter `default_model`) unless launched with a connector-wide `--model` flag.
- **MCP connection** (the AI client plays itself): the model is whatever that client runs; the server cannot set it.

The user has no supported way to choose a specific model per agent, and when a model *can't* run on their machine the failure is invisible — it surfaces only as a `[FALLBACK]` HOARD with the reason in stderr (`is_connector_fallback` is a bare boolean on the move; the reason never reaches the server). This is the same silent-failure class that made the PR #569 bug hard to find.

## Design decisions already made (discovery)

Settled with the operator before this spec; do not relitigate:

1. **The join/seat page stays provider-only.** No model picker is added there.
2. **The model decision moves server-side** as a three-layer fallback (below). The connector's hardcoded defaults become a true last resort.
3. **The per-agent model is optional and advanced**, set on the agent settings page, guarded by `model_for_provider` (applied only when it matches the seat's chosen provider).
4. **MCP is display-only for model** — the client decides; we show "runs your client's model."
5. **Fail-fast, not fail-soft.** Only the local connector can know whether a model runs on the user's CLI login, so the connector tests it and reports; the website surfaces it. A model that can't run is shown as an actionable error — never silently swapped for a different model.
6. **Fail-loud at play time too** — a model failure during a real turn surfaces its reason on connection/agent status and tags the fallback move.

## Model resolution (server-driven, three layers)

**This feature REPLACES the current model source.** Today the payload model is built at `app/engine/agent_play_next_turn.py:503` as `model_for_provider(player.chosen_provider, version.model)` — sourced from the legacy `AgentVersion.model`. That `version.model` input is **removed** from resolution; `Agent.preferred_model` takes its place. No hidden fourth layer survives.

For a machine-connection turn the server resolves the payload `model`, in order:

1. **Per-agent preferred model** (`Agent.preferred_model`), if set AND `model_for_provider(chosen_provider, preferred)` keeps it (belongs to the seat's chosen provider). **Resolution does NOT consult verification status** — a verified-failing model is handled by the join guard (FR-014) and the UI status (FR-007), never by silently swapping it for the default here. (This is what keeps FR-008's "never silently substitute" true: a chosen, provider-matching model is always what's sent, so resolution is stable turn-to-turn for a fixed preferred model; if it can't actually run, the turn fails *loud* per FR-009, it is not quietly replaced.)
2. else the **server per-provider default** for the seat's provider (only for providers with a non-empty `PROVIDER_MODELS` allowlist).
3. else **None** — the connector falls back to its own built-in default. To keep layer 3 a true last resort (and stop the connector default from silently drifting from `PROVIDER_MODELS`), the server MUST always send a concrete model for any provider with a non-empty allowlist; the connector default applies only when the server sent nothing (old server / unknown provider).

This is exactly `resolve_seat_model` (already shipped in slice 1): preferred-if-it-matches → provider default → None. Slice 2+ adds verification *status* on top, surfaced in the UI and the join guard — it does not change resolution.

**The connector `--model` flag is demoted.** Today `--model` (a connector-wide CLI flag) wins over the payload for *every* agent (`_resolve`, `agentludum_connector.py:787`), which would silently defeat per-agent selection and the verified-failing guard. After this feature, the server-resolved payload model is authoritative; `--model` only supplies the connector's local default when the server sent nothing (it moves below the payload in `_resolve`). See *Open decisions* — this is the recommended default.

**Provider-mismatch and unset are *expected* states** that fall through quietly (layers 2–3). **A verified-failing chosen model is NOT one of these** — it is surfaced as an error to fix, and the seat is guarded against it (below). **"Never silently substitute"** means: the connector never quietly runs a *different* model in place of the operator's chosen one and presents it as if it were the choice. MCP turns ignore all of this; the client's model is used.

## Reporting channels (new transport — not a reuse of the existing one)

The current self-report (`/api/agent/report-pid`: pid + hostname + detected_providers, fire-and-forget 204) and the move submit/message bodies (`is_connector_fallback` boolean only) **cannot** carry this feature. Two concrete additions are required, and both must be a **dedicated verification endpoint**, NOT a field on the turn poll — because when a connection has no live turn the connector takes an early `sleep; continue` and discards the poll body (`agentludum_connector.py` idle branch), exactly the pre-match state US3 must cover:

- **Down-channel (server → connector): the verification worklist.** A new endpoint the connector calls on a **dedicated short verification cadence (~60s when idle)** — NOT the 300s `_DETECT_REPORT_INTERVAL` PID-report hook — returning the set of `(provider, model)` pairs to verify for its connection, independent of any live turn. The short cadence is what makes SC-001's ~60s target reachable.
- **Up-channel (connector → server): verification results + play-time failure reasons.** A new endpoint carrying, per `(provider, model)`: outcome (verified / failed / timeout) and the (bounded, sanitized) error text. The **play-time failure reason MUST travel on this up-channel, not on the submit body**, because a turn that misses its deadline returns without submitting at all (`_decide` returns `None` → no POST) — a reason attached to the submit would never leave the machine in exactly the hang-caused-the-miss case. This is distinct from the existing `is_connector_fallback` flag, which says *that* a fallback happened, not *why*.

The plan picks exact paths/shapes; the spec's requirement is two dedicated channels carrying the named data, independent of the turn poll.

## User stories

### User Story 1 — Choose a model for an agent (Priority: P1)

As a bot operator, I want to optionally set a specific AI model for one of my agents, so I can run a chosen strategy on a model I pick (e.g. compare the same strategy on Haiku vs Opus) instead of always getting the provider default.

**Independent test**: On an agent's settings page, set a preferred model and save; confirm it persists and is shown; confirm the join page is unchanged (provider-only).

**Acceptance scenarios**:
1. **Given** an agent with no preferred model, **When** the operator opens its settings page, **Then** they see an optional "Preferred model (advanced)" control listing only models from `PROVIDER_MODELS`, defaulting to "Provider default."
2. **Given** the operator selects `claude-opus-4-8` and saves, **When** they reload, **Then** the selection persists, labeled "used by machine connections only; ignored by MCP."
3. **Given** any agent, **When** the operator visits the join page, **Then** no model picker appears.

### User Story 2 — The chosen model actually runs on a machine connection (Priority: P1)

As a bot operator, when I run that agent through my machine connection on the matching provider, I want the connector to run my chosen model — and fall through to a sensible default when my choice doesn't apply — so my selection takes effect without breaking play.

**Independent test**: With a verified preferred model, seat the agent on the matching provider; confirm the connector invokes that model. Seat on a non-matching provider; confirm provider default, no error.

**Acceptance scenarios**:
1. **Given** preferred model `claude-opus-4-8` seated as Claude (verified), **When** the connector plays, **Then** it runs the Claude CLI with `--model claude-opus-4-8`.
2. **Given** that agent seated as OpenAI, **When** the connector plays, **Then** the preferred model is ignored and the server's OpenAI default is used (no 404, no dead turn).
3. **Given** no preferred model, **When** the connector plays, **Then** the server's per-provider default is used; the connector built-in default applies only if the server sent no model.

### User Story 3 — Fail fast: know a model can't run before it matters (Priority: P1)

As a bot operator, I want to be told — at setup, in the UI, with an actionable reason — when a model I picked can't run on my machine, so I can fix it before a real match is affected.

**Independent test**: Set a model the local login can't run; confirm the connector reports failure and the UI shows ❌ with the real error. Set a runnable model; confirm ✅ verified.

**Acceptance scenarios**:
1. **Given** the operator sets a preferred model and a live connector hasn't verified it yet, **When** they view its status, **Then** it shows "⏳ checking on your connector."
2. **Given** the connector's test call succeeds, **When** it reports back, **Then** the status shows "✅ verified."
3. **Given** the connector's test call fails, **When** it reports back, **Then** the status shows "❌ can't run: <reason>" with guidance, and the system does not substitute a different model.
4. **Given** a verified result and no change, **When** the connector polls again, **Then** it does not re-test; it re-tests only on a model change or on the periodic refresh interval (FR-016).
5. **Given** no connector has ever polled this connection, **When** the operator views status, **Then** it shows "waiting for your connector" — distinct from "checking" — and never implies success.

### User Story 4 — Fail loud at play time (Priority: P2)

As a bot operator, if a model breaks mid-game, I want the dead turn to carry a visible reason rather than a silent HOARD, so I can tell a real decision from a failure.

**Acceptance scenarios**:
1. **Given** a model that fails during a live turn, **When** the connector submits the forced fallback, **Then** the failure reason reaches connection/agent status (not only stderr), and that model's verification flips to ❌ failed.
2. **Given** that fallback, **When** the operator views status, **Then** the move is distinguishable from a deliberate HOARD.

### User Story 5 — See what model is actually running (Priority: P2)

As a bot operator setting up a seat, I want to see which model will run for each play path, so the model in use is never a mystery.

**Acceptance scenarios**:
1. **Given** a machine-connection seat, **When** the operator views the setup surface, **Then** it shows the effective model (e.g. "runs claude-opus-4-8"); when the server sent no model (layer 3), it shows the provider's default name, not a blank.
2. **Given** an MCP seat, **When** the operator views the setup surface, **Then** it shows "runs your client's model."

## Functional requirements

- **FR-001**: Allow an operator to set/clear an optional preferred model per agent, chosen only from `PROVIDER_MODELS` (`app/config.py`). Default = none ("provider default"). (US1)
- **FR-002**: Store the preferred model on a mutable per-agent field (`Agent.preferred_model`), NOT a new immutable `AgentVersion`. (US1)
- **FR-003**: Resolve the payload model via the three-layer fallback in *Model resolution*, sourced from `Agent.preferred_model`; the legacy `version.model` input is removed, and the connector `--model` flag is demoted below the payload. (US2)
- **FR-004**: Define a per-provider default model derived from `PROVIDER_MODELS`, and send it in the payload for every provider **with a non-empty allowlist**. Providers with an empty allowlist (hermes, openclaw — MCP-only) send no server default. (US2)
- **FR-005**: The connector MUST verify a `(provider, model)` it would run with a cheap, low-token test call against the user's CLI login, driven by the down-channel worklist, independent of whether an agent currently has a live turn. The verification call MUST use its own short timeout (default ~30s, not the 180s turn ceiling) and MUST run in a path isolated from live turns — it must not consume a live-turn concurrency slot or burn a turn's deadline. **Success predicate**: `verified` = the test call exits 0 with non-empty output (the *runnability* check — deliberately looser than the move-parse path, so a model that runs but returns non-JSON still counts as runnable); a clean model-unavailable/unauthorized error = `failed`; a timeout/transport/PATH error = `timeout`. (US3)
- **FR-006**: The connector MUST report each verification outcome plus bounded error text via the dedicated up-channel endpoint; the server caches it keyed by `(connection, provider, model)`. (US3)
- **FR-007**: Show per-model verification status — checking / verified / failed-with-reason / timeout — wherever a preferred model is chosen, plus a distinct "waiting for your connector" when no connector has reported. (US3)
- **FR-008**: Never silently run a *different* model in place of a verified-failing chosen model. A verified-failing preferred model is surfaced as an error; the seat is guarded (FR-014). The layer-2/3 fallback applies only to provider-mismatch and unset (expected states), never to mask a verified-failing choice. (US3)
- **FR-009**: On a model failure during a live turn, the connector MUST send the failure reason on the **up-channel** (not the submit body, which a missed-deadline turn never sends) so it appears on connection/agent status, MUST tag any forced fallback move, AND MUST update that `(provider, model)`'s cached status. (US4)
- **FR-009a**: The connector MUST classify a play-time failure from the only signals it has (exit code + stderr text), with a conservative default. Map to sticky **failed** only when stderr clearly indicates model-unavailable/unauthorized (matches patterns like "model", "not found", "404", "unauthorized", "not available", "no access"). Map to **timeout/retryable** for: `TimeoutExpired`, CLI-missing-from-PATH (`FileNotFoundError`/exit 127), network errors, and — **the default for any unclassifiable error** (non-JSON output, generic non-zero exit, parse failures) — so a blip or an odd-but-runnable response is never reported as a permanent "can't run." A later successful verification supersedes a prior failed/timeout. (US4)
- **FR-010**: Setup surfaces MUST display the effective model read-only via a **new** value (do NOT reuse `Player.model_self_report`, which today stores the *provider* and feeds the public "played by" badge) — the resolved model for machine seats (the provider default name when layer 3 applies; for empty-allowlist machine seats, the provider's own default label), and "your client's model" for MCP seats. (US5)
- **FR-011**: The join/seat page MUST remain provider-only. (US1)
- **FR-012**: The preferred-model control MUST be labeled advanced and "used by machine connections only; ignored by MCP." (US1, US5)
- **FR-013**: The verification status enum MUST include a distinct **timeout/retryable** value, separate from a clean **failed**; timeout is retried with a bound — after N consecutive timeouts (default 3) it is shown as failed so it never sits in a silent retry loop. (US3, edge)
- **FR-014**: When an agent has a preferred model, the join flow MUST **warn** (not hard-block) only when that model is **verified-failing on every** live machine connection covering the chosen provider — i.e. at least one such connection reports it `failed` and none reports it `verified`. A not-yet-checked model (`unknown`/`checking`) MUST NOT warn, so a freshly-set model never cries wolf. MCP and paused connections are excluded from this union. Because the serving connection is not known at join and a user may have several machine connections, the guard reads the union of the user's live machine connections' `(connection, provider, model)` statuses (a new read path the join context must gain), not a single connection. No model picker is added. (US3) (See *Open decisions*.)
- **FR-015**: Error text shown in the UI MUST be length-bounded and sanitized — concretely, capped at 300 characters and stripped of absolute filesystem paths and token-shaped substrings (e.g. `sk_…`, bearer tokens) — while preserving enough of the message to be diagnostic. (US3, security)
- **FR-016**: Verification results MUST carry a last-checked timestamp; the connector MUST re-verify on a defined periodic interval (default: every 6 hours) and whenever the model set changes, so a stale "verified" cannot persist indefinitely after a login silently expires. (US3, anti-stale)
- **FR-017**: If a preferred model is later removed from `PROVIDER_MODELS` (deprecated), the system MUST treat the agent as unset (fall to provider default), clear any stale verified status for it, and show a notice on the agent settings page. (edge)
- **FR-018**: If a model's cached status flips to failed/timeout *during* a match the agent is already seated in, that match keeps playing with clearly-tagged fallback moves (per FR-009) — the running seat is not pulled — and the failure is surfaced so the operator can fix it for future matches. (edge)

## Key entities

- **Per-agent preferred model** — a nullable, mutable `preferred_model` on `Agent` (independent of the versioned strategy). NULL = "provider default."
- **Model verification result** — keyed by **(connection, provider, model)** (not per-agent: a login either can or cannot run a model regardless of which agent uses it; agents sharing a model share the result). Fields: status (`unknown` / `checking` / `verified` / `failed` / `timeout`), bounded error text, last-checked timestamp. Stored in a **new store** (e.g. a `model_verifications` table), NOT the `connection_providers` row, which is unique per `(connection, provider)` and cannot hold multiple models.
- **Server per-provider default model** — provider → default model, sourced from `PROVIDER_MODELS` (first entry unless the plan defines an explicit map); absent for empty-allowlist providers.

## Out of scope / non-goals

- Any model picker on the join/seat page (provider-only stays).
- Setting/controlling the model for MCP connections (the client decides; we only display it).
- Making the per-agent model mandatory or re-coupling agents to a required model.
- Per-turn or per-match model overrides (the model is per-agent).
- Letting operators choose model names outside `PROVIDER_MODELS`.

## Edge cases

- **Preferred model deprecated from `PROVIDER_MODELS`** → treat as unset, clear stale verified, show a notice (FR-017).
- **MCP-only operator (no connector)** → preferred-model field is moot; status shows "waiting for your connector," never success (FR-007).
- **Model changed mid-match** → connector picks up the new model on its next turn; do not crash the in-flight chained session (verification test runs in a path isolated from the live session).
- **Connector live but not yet verified** vs **no connector polling** → distinct UI states (FR-007); "checking" is bounded by the poll cycle, not indefinite (SC-001).
- **Verification call times out** (vs clean "not available") → `timeout` status, retried (FR-013), not a permanent ❌.
- **Empty-allowlist provider** (hermes/openclaw) → no model control, no server default (FR-004).
- **Verified-failing model seated anyway** (stale verification) → fail loud: tagged fallback + reason surfaced + status flipped to failed (FR-009); never a silent different-model substitution.
- **Two agents, same preferred model, same provider** → share one verification result; "one AI = one game" still governs seating.

## Acceptance criteria (feature-level)

1. Operator can set/clear a preferred model per agent from `PROVIDER_MODELS`; the join page is unchanged.
2. The resolved machine-connection model follows the three-layer fallback; a provider-mismatched model never reaches the CLI; the server always sends a model for non-empty-allowlist providers.
3. The connector verifies models from a worklist (no live turn required) and reports verified/failed/timeout + bounded reason; the UI shows all four states plus "waiting for your connector."
4. Verification is cached, re-tested on change and every 6h, and a stale verified is cleared on any play-time failure.
5. A verified-failing model is never silently swapped; the operator sees an actionable error and the seat is guarded at join (FR-014).
6. A live-turn model failure surfaces its reason on status, tags the fallback, and flips verification to failed.
7. MCP seats show "runs your client's model" and ignore the preferred model.
8. UI reason text is bounded and sanitized.
9. Preflight Gate (ruff + mypy + pytest) green; new `app/engine` logic has tests.

## Success criteria

- **SC-001**: When a connector is live and polling, a newly set model reaches a definitive verified/failed/timeout state within **~60 seconds** of the connector's next verification-cadence tick (a wall-clock target, since poll cycles vary); the UI never shows "checking" indefinitely while a connector is live.
- **SC-002**: A model the login can't run produces zero *silent* dead-turns — caught at setup (US3) or, if it slips to play, every affected turn carries a visible reason and flips the status (US4).
- **SC-003**: Operators who never set a preferred model see no behavior change and no new required steps.
- **SC-004**: No model that mismatches a seat's provider is ever passed to that provider's CLI.
- **SC-005**: A login that silently expires after a prior ✅ is re-flagged within the refresh interval (FR-016) or on the next failed turn — a stale ✅ cannot mask a broken login forever.

## Assumptions carried into the plan

- **Storage**: nullable `Agent.preferred_model` (mutable); a new `model_verifications` store keyed by `(connection, provider, model)`. Plan confirms exact tables/migrations.
- **Down/up channels**: plan picks exact endpoint/field shapes for the verification worklist (down) and results + play-time reason (up); both are new schema. The effective-model display (FR-010) uses a **new** value, NOT `Player.model_self_report` (which is unused/`None` at join; the public "played by" badge reads `Player.played_provider`). The legacy payload fields `preferred_model`/`preferred_provider` are not consulted by the new resolution and are treated as retired.
- **UI home**: agent settings page hosts the control + status; connections page may mirror per-provider model status. Join page untouched.
- **Server default**: first entry of each non-empty `PROVIDER_MODELS` list unless the plan defines an explicit map.
- **Verification test call**: a minimal one-token prompt per provider CLI (e.g. `claude --model <m> --print "ok"`), run in a path isolated from any live chained session, cached by `(connection, provider, model)`, refreshed on change and every 6h.

## Open decisions for the operator

Resolved here with recommended defaults; flagged because they are genuine product/architecture choices the reviews surfaced:

1. **Connector `--model` flag precedence.** *Recommended (encoded above):* the server-resolved per-agent/default model wins; `--model` is demoted to a local fallback only when the server sends nothing. Alternative: keep `--model` as an explicit operator override that beats the server (simpler, but it silently defeats per-agent selection and the guard). 
2. **Join guard: warn vs hard-block, across multiple connections.** *Recommended (encoded above):* **warn** if the model isn't verified-runnable on any of the user's live machine connections for that provider; do not hard-block (a hard block is brittle when a user has several connections and the serving one isn't known until claim time). Alternative: hard-block when no live connection can run it.
3. **Two machines, one connection, different logins.** Verification is keyed `(connection, provider, model)` with no machine dimension, so two laptops sharing a connection key but with different model access would overwrite each other's result (last-writer-wins). *Recommended:* accept for now (document it); revisit if multi-machine connections become common.

## Review reconciliation (spec checkpoint)

Spec adversarial reviews ran in two rounds. The Gemini (`requirements`) CLI is **dead on this machine** (deprecated for individual accounts — `IneligibleTierError`), so the run uses the repo's Claude-reviewer path (spec 020) for the Gemini-equivalent lens throughout. **Round 1:** Codex (`feasibility`) + a Claude `requirements` subagent. **Round 2:** Claude `feasibility` + `requirements` subagents on the revised spec.

Round-2 findings addressed: removed `version.model` from resolution (*Model resolution*, FR-003); committed the verification channels to a dedicated endpoint independent of the turn poll and routed the play-time reason on the up-channel, not the submit (*Reporting channels*, FR-009); demoted the connector `--model` flag (*Model resolution*, FR-003); resolved the multi-connection join guard to a warning over live connections (FR-014); replaced the `model_self_report` reuse with a new display value (FR-010); added a verification timeout/isolation budget (FR-005), failure classification (FR-009a), timeout-retry bound (FR-013), in-flight-match behavior (FR-018), and a wall-clock SC-001.

Round-1 findings addressed: 

- **Reporting transport can't be reused as-is (Codex HIGH, Claude HIGH×2)** → new *Reporting channels* section; FR-005/FR-006/FR-009 now require explicit down/up channels and separate the failure *reason* from the existing `is_connector_fallback` flag.
- **Verification record dimensionality (Claude HIGH)** → resolved: keyed `(connection, provider, model)` in a new store, not the per-provider row (*Key entities*).
- **Connector-default drift (Codex MEDIUM)** → FR-003/FR-004: server always sends a model for non-empty-allowlist providers; connector default is last-resort only.
- **Verified-failing runtime behavior (Claude MEDIUM)** → FR-008/FR-009/FR-014 + *Model resolution*: guard at join, fail loud at play, never substitute.
- **Stale verified = silent-failure recurrence (Claude MEDIUM, top residual risk)** → FR-016 (6h refresh + clear-on-failure) + SC-005.
- **Unbounded "checking" / timing (Claude MEDIUM)** → SC-001 (2 poll cycles) + FR-007 "waiting for your connector" state.
- **Timeout vs failed (Claude MEDIUM)** → FR-013 adds a `timeout` status.
- **`model_self_report` reuse (Claude MEDIUM)** → FR-010 reuses it for display (reuse audit will confirm at plan stage).
- **Reason text safety (Claude LOW)** → FR-015 bound + sanitize.
- **FR-004 vs empty allowlist (Claude LOW)** → FR-004 excludes hermes/openclaw.
- **Deprecated preferred model (Claude LOW)** → FR-017.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections.