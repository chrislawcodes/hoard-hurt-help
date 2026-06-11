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

try:
    # In a source checkout, reuse the game's canonical protocol. Downloaded
    # standalone connectors use the embedded compatibility copy below.
    from app.agent_prompt import RESPONSE_PROTOCOL as _CANONICAL_PROTOCOL
except ImportError:
    _CANONICAL_PROTOCOL = None

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
            "message": _clip(move.get("message", ""), 200),
            "thinking": _clip(move.get("thinking", ""), 200),
        }
    return {
        "action": str(move.get("action", "HOARD")).upper(),
        "target_id": move.get("target_id") or None,
        "thinking": _clip(move.get("thinking", ""), 200),
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
        argv = ["codex", "exec"]
        if resume_id:
            argv += ["resume", resume_id]
        # read-only sandbox: a game move needs no file writes and no network, so
        # lock the model's shell tool to reads only. (Codex writes the
        # --output-last-message file itself, outside the sandbox, so capture
        # still works.)
        argv += ["--sandbox", "read-only", "--json", "--skip-git-repo-check", "--model", model]
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
                "message": _clip(decision.get("message", ""), 200),
                "thinking": _clip(decision.get("thinking", ""), 200),
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
            "thinking": _clip(decision.get("thinking", ""), 200),
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
