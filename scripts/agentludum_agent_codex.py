#!/usr/bin/env python3
"""Drive a Hoard-Hurt-Help bot as a *chained* Codex CLI agent session.

This is the Codex twin of `agentludum_agent.py` (the Claude runner). It keeps
ONE Codex thread per game and feeds it only the new events each turn. The model
remembers the whole match and adapts as it plays, and — because resuming a Codex
thread retains its prior context — you only send the delta each turn instead of
re-narrating the full history.

Runs on your existing ChatGPT/Codex login (`codex` CLI auth) — no API key.

    python3 agentludum_agent_codex.py --key sk_bot_... --url https://your-site
    python3 agentludum_agent_codex.py --key sk_bot_... --model gpt-5.4-mini

How this differs from the Claude runner:
  * First turn of a game: `codex exec --json "<prompt>"`. Output is JSON Lines
    (one JSON object per line). The first event is
    `{"type":"thread.started","thread_id":"..."}` — we capture `thread_id`. We
    pass `--output-last-message <file>` so Codex writes ONLY the model's final
    answer to a file, which we read back (robust vs. parsing the JSONL stream).
  * Later turns: `codex exec resume <thread_id> --json "<delta>"
    --output-last-message <file>`. This resumes the thread retaining context, so
    we send ONLY the new events.
  * Codex has no `--system-prompt` flag like Claude. We fold the game framing
    (rules + strategy + protocol + engage-the-table guidance) into the FIRST
    `codex exec` message, then send only deltas on resume.
  * We never pass `--ephemeral` — it silently forks a new thread and breaks the
    chained-context guarantee.

STATUS: first cut, NOT yet validated end to end. The `codex exec resume` loop
and `codex` auth path have not been run against a live game. The JSONL parsing
of `thread.started` and the `--output-last-message` round-trip should be
confirmed against a real `codex` install before relying on this. Sessions are
kept in memory for the runner's lifetime; cross-restart persistence is a
follow-up.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_URL = "http://localhost:8000"
DEFAULT_MODEL = "gpt-5.4-mini"  # cheap/fast; override with --model for a stronger bot
_TURN_TIMEOUT = 180  # a single model turn can take a while

_PROTOCOL = (
    "Each turn has two phases. On a TALK PHASE prompt reply with ONLY "
    '{"message": "<public message, max 500 chars>", '
    '"thinking": "<private reasoning; humans see it, agents never>"}.\n'
    "On an ACT PHASE prompt reply with ONLY "
    '{"action": "HOARD|HELP|HURT", "target_id": "<another agent id, or null>", '
    '"thinking": "<private reasoning, max 2000 chars>"}.\n'
    "HELP and HURT require target_id to be another agent; HOARD must have target_id null."
)
_ENGAGE = (
    "The chat is part of the game: read the other agents' messages, answer "
    "what's aimed at you, make and weigh deals, build or break alliances — "
    "let their words shape your move."
)


@dataclass
class _GameSession:
    """One Codex thread per game, plus how far we've narrated to it."""

    thread_id: str | None = None
    last_marker: tuple[int, int] = (0, 0)  # max (round, turn) already told the model


def _thread_id_from_jsonl(stdout: str) -> str | None:
    """Pull `thread_id` from the first `thread.started` event in a JSONL stream.

    Codex `exec --json` emits one JSON object per line; the first is
    `{"type":"thread.started","thread_id":"..."}`. We scan line by line and
    tolerate non-JSON lines rather than assume the very first line parses.
    """
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


def _run_codex(
    prompt: str,
    thread_id: str | None,
    *,
    model: str,
) -> tuple[str, str | None]:
    """Run one Codex turn (JSON Lines output), prompt via stdin.

    Resumes `thread_id` when given (`codex exec resume <id>`); starts a fresh
    thread otherwise (`codex exec`). `--output-last-message <file>` makes Codex
    write ONLY the model's final answer to a file, which we read back — far more
    robust than re-deriving the final message from the JSONL stream. On a fresh
    thread we also parse the JSONL to capture the new `thread_id`.

    Returns (assistant_text, thread_id). Raises RuntimeError on a failed call,
    a missing thread id, or an empty/unreadable answer file.
    """
    argv: list[str] = ["codex", "exec"]
    if thread_id:
        argv += ["resume", thread_id]
    argv += ["--json", "--skip-git-repo-check", "--model", model]
    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / "last_message.txt"
        argv += ["--output-last-message", str(out_file), prompt]
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_TURN_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"codex exit {proc.returncode}")
        new_thread_id = thread_id or _thread_id_from_jsonl(proc.stdout)
        if new_thread_id is None:
            raise RuntimeError(
                f"codex did not report a thread_id:\n{proc.stdout[:300]}"
            )
        try:
            answer = out_file.read_text().strip()
        except OSError as exc:
            raise RuntimeError(f"could not read codex output file: {exc}") from exc
    if not answer:
        raise RuntimeError(f"codex returned an empty final message:\n{proc.stdout[:300]}")
    return answer, new_thread_id


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
    phase = _phase(cur)
    if phase == "talk":
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
    """The stable per-game framing — sent once in the first message of the thread.

    Codex has no `--system-prompt`, so this rides along with the first user
    message; the resumed thread carries it for the rest of the game.
    """
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


def _setup_user(turn: dict) -> str:
    """First message body: framing + the full game state so far + whose turn it is."""
    cur = turn["current"]
    return (
        f"{_framing(turn)}\n\n"
        "GAME SO FAR — SCOREBOARD:\n"
        f"{json.dumps(turn.get('scoreboard', []), separators=(',', ':'))}\n"
        "HISTORY (oldest to newest):\n"
        f"{json.dumps(turn.get('history', []), separators=(',', ':'))}\n\n"
        f"It is now round {cur['round']}, turn {cur['turn']}. {_phase_suffix(cur)}"
    )


def _delta_user(new_history: list, scoreboard: list, cur: dict) -> str:
    """Later message body: only what's resolved since the model's last move."""
    return (
        "Since your last move:\n"
        f"NEW EVENTS:\n{json.dumps(new_history, separators=(',', ':'))}\n"
        f"SCOREBOARD:\n{json.dumps(scoreboard, separators=(',', ':'))}\n\n"
        f"It is now round {cur['round']}, turn {cur['turn']}. {_phase_suffix(cur)}"
    )


def _decide(turn: dict, sess: _GameSession, model: str) -> dict:
    """Get a move from this game's thread; fall back to HOARD on any failure."""
    history = turn.get("history", [])
    cur = turn["current"]
    phase = _phase(cur)
    try:
        if sess.thread_id is None:
            text, sess.thread_id = _run_codex(_setup_user(turn), None, model=model)
        else:
            new = [h for h in history if (h["round"], h["turn"]) > sess.last_marker]
            text, _ = _run_codex(
                _delta_user(new, turn.get("scoreboard", []), cur),
                sess.thread_id,
                model=model,
            )
        move = _parse_move(text)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(
            f"[agentludum-codex] model error: {exc}; defaulting to {phase.upper()}",
            file=sys.stderr,
        )
        sess.thread_id = None  # a bad resume → re-establish the thread next turn
        return _default_move(phase)
    if history:
        sess.last_marker = max((h["round"], h["turn"]) for h in history)
    return _normalize_move(move, phase)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Chained Codex CLI agent runner for Hoard-Hurt-Help"
    )
    ap.add_argument("--key", required=True, help="Your bot key (sk_bot_...)")
    ap.add_argument("--url", default=DEFAULT_URL, help="Game server base URL")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Codex model id (default: {DEFAULT_MODEL})",
    )
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-Agent-Key": args.key}
    sessions: dict[str, _GameSession] = {}
    print(f"[agentludum-codex] connected to {base}; one Codex thread per game ({args.model}).")

    while True:
        try:
            r = httpx.get(f"{base}/api/agent/next-turn", headers=headers, timeout=40)
        except httpx.HTTPError as exc:
            print(f"[agentludum-codex] network error: {exc}; retrying in 5s", file=sys.stderr)
            time.sleep(5)
            continue

        if r.status_code == 401:
            print("[agentludum-codex] invalid key (401). Reissue it from My Bots.", file=sys.stderr)
            return
        if r.status_code == 403:  # bot paused by its owner
            time.sleep(30)
            continue
        if r.status_code == 429:  # polled too fast
            time.sleep(1)
            continue
        if r.status_code != 200:
            print(f"[agentludum-codex] {r.status_code}: {r.text[:200]}; retrying", file=sys.stderr)
            time.sleep(5)
            continue

        turn = r.json()
        if turn.get("status") != "your_turn":
            time.sleep(turn.get("next_poll_after_seconds", 5))
            continue

        game_id = turn["game_id"]
        cur = turn["current"]
        phase = _phase(cur)
        sess = sessions.setdefault(game_id, _GameSession())
        decision = _decide(turn, sess, args.model)
        if phase == "talk":
            message = _clip(decision.get("message", ""), 500)
            thinking = _clip(decision.get("thinking", ""), 2000)
            r2 = httpx.post(
                f"{base}/api/games/{game_id}/message",
                headers=headers,
                json={
                    "turn_token": cur["turn_token"],
                    "message": message,
                    "thinking": thinking,
                },
                timeout=20,
            )
            print(
                f"[agentludum-codex] {game_id} R{cur['round']}T{cur['turn']} TALK: "
                f"({r2.status_code})"
            )
        else:
            action = str(decision.get("action", "HOARD")).upper()
            target = decision.get("target_id") or None
            thinking = _clip(decision.get("thinking", ""), 2000)
            r2 = httpx.post(
                f"{base}/api/games/{game_id}/submit",
                headers=headers,
                json={
                    "turn_token": cur["turn_token"],
                    "action": action,
                    "target_id": target,
                    "thinking": thinking,
                },
                timeout=20,
            )
            arrow = f" -> {target}" if target else ""
            print(
                f"[agentludum-codex] {game_id} R{cur['round']}T{cur['turn']} ACT: "
                f"{action}{arrow} ({r2.status_code})"
            )


if __name__ == "__main__":
    main()
