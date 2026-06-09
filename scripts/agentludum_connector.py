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


# Adapter registry keyed by the server's provider value.
_ADAPTERS: dict[str, _ClaudeAdapter | _CodexAdapter | _GeminiAdapter] = {
    "claude": _ClaudeAdapter(),
    "openai": _CodexAdapter(),
    "gemini": _GeminiAdapter(),
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


def _resolve(turn: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Pick the provider and model for a turn.

    Legacy payloads may still supply `preferred_provider` / `preferred_model`.
    New payloads supply `model` plus `agent_id` / `match_id`.
    """
    legacy_provider = (turn.get("preferred_provider") or "").lower()
    turn_provider = _provider_from_model(turn.get("model")) or _provider_from_model(
        turn.get("preferred_model")
    )
    provider = args.provider or (legacy_provider if legacy_provider in _ADAPTERS else None)
    if provider is None:
        provider = turn_provider
    if provider not in _ADAPTERS:
        if legacy_provider and not args.provider:
            print(
                f"[agentludum-connector] turn is configured for {legacy_provider!r}, which has no "
                f"CLI runner — using {DEFAULT_PROVIDER}. (MCP-only providers do not use this runner.)",
                file=sys.stderr,
            )
        provider = DEFAULT_PROVIDER
    adapter = _ADAPTERS[provider]
    if args.model:
        model = args.model
    elif args.provider:
        model = adapter.default_model
    elif provider == legacy_provider:
        model = str(turn.get("preferred_model") or adapter.default_model)
    elif provider == turn_provider:
        model = str(turn.get("model") or adapter.default_model)
    else:
        model = adapter.default_model
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


def _decide(turn: dict, sess: _GameSession) -> dict:
    """Get a move from this game's chained session; fall back to HOARD on any
    failure (and drop the session so the next turn re-establishes it)."""
    adapter = _ADAPTERS[str(sess.provider)]
    history = turn.get("history", [])
    cur = turn["current"]
    phase = _phase(cur)
    try:
        if sess.token is None:
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
        print(
            f"[agentludum-connector] {sess.provider} model error: {exc}; defaulting to {phase.upper()}",
            file=sys.stderr,
        )
        sess.token = None  # a bad resume → re-establish the session next turn
        return _default_move(phase)
    if usage:
        _record_usage(_turn_match_id(turn), cur, usage, sess)
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
            json={"pid": pid},
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        print(f"[agentludum-connector] could not report PID {pid}: {exc}", file=sys.stderr)
    print(
        f"[agentludum-connector] connected to {base}; PID {pid}; one chained session per agent+match."
    )

    while True:
        try:
            r = httpx.get(f"{base}/api/agent/next-turn", headers=headers, timeout=40)
        except httpx.HTTPError as exc:
            print(f"[agentludum-connector] network error: {exc}; retrying in 5s", file=sys.stderr)
            time.sleep(5)
            continue

        if r.status_code == 401:
            print(
                "[agentludum-connector] invalid key (401). Reissue it from My Connections.",
                file=sys.stderr,
            )
            return
        if r.status_code == 403:  # connection paused by its owner
            time.sleep(30)
            continue
        if r.status_code == 429:  # polled too fast
            time.sleep(1)
            continue
        if r.status_code != 200:
            print(
                f"[agentludum-connector] {r.status_code}: {r.text[:200]}; retrying",
                file=sys.stderr,
            )
            time.sleep(5)
            continue

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
        if phase == "talk":
            r2 = httpx.post(
                f"{base}/api/games/{match_id}/message",
                headers=headers,
                json={
                    "turn_token": cur["turn_token"],
                    "message": _clip(decision.get("message", ""), 500),
                    "thinking": _clip(decision.get("thinking", ""), 2000),
                },
                timeout=20,
            )
            print(
                f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} TALK: "
                f"({r2.status_code})"
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
                },
                timeout=20,
            )
            arrow = f" -> {target}" if target else ""
            print(
                f"[agentludum-connector] {match_id} R{cur['round']}T{cur['turn']} ACT: "
                f"{action}{arrow} ({r2.status_code})"
            )


if __name__ == "__main__":
    main()
