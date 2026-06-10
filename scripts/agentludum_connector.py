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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_URL = "http://localhost:8000"
DEFAULT_PROVIDER = "claude"
_TURN_TIMEOUT = 180  # a single model turn can take a while

# Circuit-breaker threshold for the poll loop. Each failed poll sleeps ~5 s, so
# 24 consecutive failures ≈ 2 minutes of a permanently unreachable server before
# we give up and exit with a non-zero code.
_POLL_FAIL_THRESHOLD = 24

_PROTOCOL = (
    "Each turn has two phases. On a TALK PHASE prompt reply with ONLY "
    '{"message": "<public message, max 500 chars>", '
    '"thinking": "<private reasoning; humans see it, agents never>"}.\n'
    "On an ACT PHASE prompt reply with ONLY "
    '{"action": "HOARD|HELP|HURT", "target_id": "<another agent id, or null>", '
    '"thinking": "<private reasoning, max 2000 chars>"}.\n'
    "Always fill in `thinking` with a real one- or two-sentence reason for your "
    "choice — never leave it empty or omit it. Humans read it; agents never do, "
    "so it costs you nothing.\n"
    "HELP and HURT require target_id to be another agent; HOARD must have target_id null."
)
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
    if _phase(cur) == "talk":
        return "TALK PHASE — JSON only"
    return f"ACT PHASE — here are this turn's messages: {_format_talk_messages(cur)} — JSON only"


def _clip(text: object, limit: int) -> str:
    return str(text or "")[:limit]


def _default_move(phase: str) -> dict:
    if phase == "talk":
        return {"message": "", "thinking": ""}
    return {"action": "HOARD", "target_id": None, "thinking": ""}


def _normalize_move(move: dict, phase: str) -> dict:
    if phase == "talk":
        return {
            "message": _clip(move.get("message", ""), 500),
            "thinking": _clip(move.get("thinking", ""), 2000),
        }
    return {
        "action": str(move.get("action", "HOARD")).upper(),
        "target_id": move.get("target_id") or None,
        "thinking": _clip(move.get("thinking", ""), 2000),
    }


def _framing(turn: dict) -> str:
    """The stable per-game framing (strategy + rules + protocol). Claude sends it
    as a `--system-prompt`; Codex/Gemini fold it into the first message."""
    static = turn["static"]
    you = static["your_agent_id"]
    others = [a for a in static.get("all_agent_ids", []) if a != you]
    strategy = static.get("your_strategy") or "Play to win."
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


def _run(argv: list[str], *, stdin_input: str | None = None) -> subprocess.CompletedProcess:
    """Run a CLI once. Prompt via stdin (claude) or argv (codex/gemini); when no
    stdin is piped we feed DEVNULL so the CLI never blocks waiting on input."""
    if stdin_input is not None:
        return subprocess.run(
            argv, input=stdin_input, capture_output=True, text=True, timeout=_TURN_TIMEOUT
        )
    return subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=_TURN_TIMEOUT
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


def _decide(turn: dict, sess: _GameSession) -> dict:
    """Get a move from this game's chained session; fall back to a default on any
    failure (and drop the session so the next turn re-establishes it).

    On failure the returned move includes ``is_connector_fallback=True`` so the
    submission layer can mark the record in the database and callers can log it.
    """
    adapter = _ADAPTERS[str(sess.provider)]
    history = turn.get("history", [])
    cur = turn["current"]
    phase = _phase(cur)
    match_id = _turn_match_id(turn)
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
    if usage:
        _record_usage(match_id, cur, usage, sess)
    if history:
        sess.last_marker = max((h["round"], h["turn"]) for h in history)
    return _normalize_move(move, phase)


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
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-Connection-Key": args.key}
    sessions: dict[tuple[str, str], _GameSession] = {}
    pid = os.getpid()
    try:
        httpx.post(
            f"{base}/api/agent/report-pid",
            headers=headers,
            json={"pid": pid, "detected_providers": _detect_providers()},
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError as exc:
        # Non-fatal: PID reporting is best-effort. The connector continues to
        # play even if the server can't record the PID for the operator.
        print(
            f"[agentludum-connector] WARNING: could not report PID {pid} to server: {exc}",
            file=sys.stderr,
        )
    print(
        f"[agentludum-connector] connected to {base}; PID {pid}; one chained session per agent+match."
    )

    # Circuit-breaker state: count consecutive failed polls. After
    # _POLL_FAIL_THRESHOLD consecutive failures we give up and exit so the
    # process doesn't spin forever against a dead server.
    consecutive_poll_failures = 0

    while True:
        try:
            r = httpx.get(f"{base}/api/agent/next-turn", headers=headers, timeout=40)
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

        turn = r.json()
        if turn.get("status") != "your_turn":
            time.sleep(turn.get("next_poll_after_seconds", 5))
            continue

        match_id = _turn_match_id(turn)
        cur = turn["current"]
        phase = _phase(cur)
        sess = _session_for_turn(turn, args, sessions)
        if sess.token is None:
            agent_name = turn.get("agent_name", turn.get("agent_id", "unknown"))
            version_no = turn.get("version_no", "?")
            print(
                f"[agentludum-connector] {match_id}: agent {agent_name} "
                f"(agent {turn.get('agent_id', 'unknown')}, v{version_no}) on {sess.provider} ({sess.model})."
            )
        decision = _decide(turn, sess)
        is_fallback = bool(decision.get("is_connector_fallback"))
        if phase == "talk":
            r2 = httpx.post(
                f"{base}/api/games/{match_id}/message",
                headers=headers,
                json={
                    "turn_token": cur["turn_token"],
                    "message": _clip(decision.get("message", ""), 500),
                    "thinking": _clip(decision.get("thinking", ""), 2000),
                    "is_connector_fallback": is_fallback,
                },
                timeout=20,
            )
            fallback_tag = " [FALLBACK]" if is_fallback else ""
            print(
                f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} TALK: "
                f"({r2.status_code}){fallback_tag}"
            )
        else:
            action = str(decision.get("action", "HOARD")).upper()
            target = decision.get("target_id") or None
            r2 = httpx.post(
                f"{base}/api/games/{match_id}/submit",
                headers=headers,
                json={
                    "turn_token": cur["turn_token"],
                    "action": action,
                    "target_id": target,
                    "thinking": _clip(decision.get("thinking", ""), 2000),
                    "is_connector_fallback": is_fallback,
                },
                timeout=20,
            )
            arrow = f" -> {target}" if target else ""
            fallback_tag = " [FALLBACK]" if is_fallback else ""
            print(
                f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} ACT: "
                f"{action}{arrow} ({r2.status_code}){fallback_tag}"
            )


if __name__ == "__main__":
    main()
