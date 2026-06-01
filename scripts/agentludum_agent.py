#!/usr/bin/env python3
"""Drive a Hoard-Hurt-Help bot as a *chained* Claude Code agent session.

Unlike the stateless runner (`agentludum_bot.py`), this keeps ONE Claude Code
session per game and feeds it only the new events each turn. The model remembers
the whole match and adapts as it plays, and — because Claude Code caches the
session prefix automatically — you don't re-pay for the full history every turn.

Runs on your existing `claude` login (Claude Code subscription) — no API key.

    python3 agentludum_agent.py --key sk_bot_... --url https://your-site
    python3 agentludum_agent.py --key sk_bot_... --model claude-sonnet-4-6

Cost notes (measured): even a stripped `claude -p` call carries ~19k tokens of
Claude Code framework overhead per call (only `--bare` removes it, and `--bare`
needs an API key, which we avoid). We minimise it three ways, all kept here:
  * `--model` defaults to Haiku (cheapest); override for a stronger player.
  * `--tools ""` drops the built-in tool definitions — this is a decision task.
  * `--system-prompt` replaces Claude Code's coding-agent prompt with our game
    framing, set once on the first turn; the session caches it after that.

STATUS: first cut. The single-turn `--resume` mechanic is verified (context is
retained), but the multi-turn loop and the real per-game cost should be measured
against a live game before relying on it. Sessions are kept in memory for the
runner's lifetime; cross-restart persistence is a follow-up.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx

DEFAULT_URL = "http://localhost:8000"
DEFAULT_MODEL = "claude-haiku-4-5"  # cheapest; override with --model for a stronger bot
_TURN_TIMEOUT = 180  # a single model turn can take a while

_PROTOCOL = (
    "Each turn I'll give you only what's happened since your last move. "
    "Reply with ONLY a JSON object, no other text:\n"
    '{"action": "HOARD|HELP|HURT", "target_id": "<another agent id, or null>", '
    '"message": "<short public message, max 200 chars>"}\n'
    "HELP and HURT require target_id to be another agent; HOARD must have target_id null."
)
_ENGAGE = (
    "The chat is part of the game: read the other agents' messages, answer "
    "what's aimed at you, make and weigh deals, build or break alliances — "
    "let their words shape your move."
)


@dataclass
class _GameSession:
    """One Claude Code session per game, plus how far we've narrated to it."""

    session_id: str | None = None
    last_marker: tuple[int, int] = (0, 0)  # max (round, turn) already told the model


def _run_claude(
    prompt: str,
    session_id: str | None,
    *,
    model: str,
    system_prompt: str | None = None,
) -> tuple[str, str | None]:
    """Run one Claude Code turn (print mode, JSON output), prompt via stdin.

    Resumes `session_id` when given; sets `system_prompt` only on the first turn
    (a resumed session already carries it). `--tools ""` keeps this a pure
    decision call with no agent tooling. Returns (assistant_text, session_id).
    Raises RuntimeError on a failed or unparseable call.
    """
    argv = ["claude", "--print", "--output-format", "json", "--model", model, "--tools", ""]
    if session_id:
        argv += ["--resume", session_id]
    if system_prompt is not None:
        argv += ["--system-prompt", system_prompt]
    proc = subprocess.run(
        argv, input=prompt, capture_output=True, text=True, timeout=_TURN_TIMEOUT
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"claude exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude returned non-JSON: {proc.stdout[:300]}") from exc
    return str(data.get("result", "")), data.get("session_id")


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


def _system_prompt(turn: dict) -> str:
    """The stable per-game framing — set once, then cached by the session."""
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
    """First user message: the full game state so far + whose turn it is."""
    cur = turn["current"]
    return (
        "GAME SO FAR — SCOREBOARD:\n"
        f"{json.dumps(turn.get('scoreboard', []), separators=(',', ':'))}\n"
        "HISTORY (oldest to newest):\n"
        f"{json.dumps(turn.get('history', []), separators=(',', ':'))}\n\n"
        f"It is now round {cur['round']}, turn {cur['turn']}. Your move — JSON only."
    )


def _delta_user(new_history: list, scoreboard: list, cur: dict) -> str:
    """Later user messages: only what's resolved since the model's last move."""
    return (
        "Since your last move:\n"
        f"NEW EVENTS:\n{json.dumps(new_history, separators=(',', ':'))}\n"
        f"SCOREBOARD:\n{json.dumps(scoreboard, separators=(',', ':'))}\n\n"
        f"It is now round {cur['round']}, turn {cur['turn']}. Your move — JSON only."
    )


def _decide(turn: dict, sess: _GameSession, model: str) -> dict:
    """Get a move from this game's session; fall back to HOARD on any failure."""
    history = turn.get("history", [])
    cur = turn["current"]
    try:
        if sess.session_id is None:
            text, sess.session_id = _run_claude(
                _setup_user(turn), None, model=model, system_prompt=_system_prompt(turn)
            )
        else:
            new = [h for h in history if (h["round"], h["turn"]) > sess.last_marker]
            text, _ = _run_claude(
                _delta_user(new, turn.get("scoreboard", []), cur),
                sess.session_id,
                model=model,
            )
        move = _parse_move(text)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[agentludum-agent] model error: {exc}; defaulting to HOARD", file=sys.stderr)
        sess.session_id = None  # a bad resume → re-establish the session next turn
        return {"action": "HOARD", "target_id": None, "message": ""}
    if history:
        sess.last_marker = max((h["round"], h["turn"]) for h in history)
    return move


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Chained Claude Code agent runner for Hoard-Hurt-Help"
    )
    ap.add_argument("--key", required=True, help="Your bot key (sk_bot_...)")
    ap.add_argument("--url", default=DEFAULT_URL, help="Game server base URL")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Claude model alias/id (default: {DEFAULT_MODEL} — cheapest)",
    )
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-Agent-Key": args.key}
    sessions: dict[str, _GameSession] = {}
    print(f"[agentludum-agent] connected to {base}; one Claude session per game ({args.model}).")

    while True:
        try:
            r = httpx.get(f"{base}/api/agent/next-turn", headers=headers, timeout=40)
        except httpx.HTTPError as exc:
            print(f"[agentludum-agent] network error: {exc}; retrying in 5s", file=sys.stderr)
            time.sleep(5)
            continue

        if r.status_code == 401:
            print("[agentludum-agent] invalid key (401). Reissue it from My Bots.", file=sys.stderr)
            return
        if r.status_code == 403:  # bot paused by its owner
            time.sleep(30)
            continue
        if r.status_code == 429:  # polled too fast
            time.sleep(1)
            continue
        if r.status_code != 200:
            print(f"[agentludum-agent] {r.status_code}: {r.text[:200]}; retrying", file=sys.stderr)
            time.sleep(5)
            continue

        turn = r.json()
        if turn.get("status") != "your_turn":
            time.sleep(turn.get("next_poll_after_seconds", 5))
            continue

        game_id = turn["game_id"]
        cur = turn["current"]
        sess = sessions.setdefault(game_id, _GameSession())
        decision = _decide(turn, sess, args.model)
        action = str(decision.get("action", "HOARD")).upper()
        target = decision.get("target_id")
        message = (decision.get("message") or "")[:200]

        r2 = httpx.post(
            f"{base}/api/games/{game_id}/submit",
            headers=headers,
            json={
                "turn_token": cur["turn_token"],
                "action": action,
                "target_id": target,
                "message": message,
            },
            timeout=20,
        )
        arrow = f" -> {target}" if target else ""
        print(
            f"[agentludum-agent] {game_id} R{cur['round']}T{cur['turn']}: "
            f"{action}{arrow} ({r2.status_code})"
        )


if __name__ == "__main__":
    main()
