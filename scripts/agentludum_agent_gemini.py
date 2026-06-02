#!/usr/bin/env python3
"""Drive a Hoard-Hurt-Help bot as a *chained* Gemini CLI agent session.

This is the Gemini twin of `agentludum_agent.py` (the Claude runner) and
`agentludum_agent_codex.py` (the Codex runner). It keeps ONE Gemini session per
game and feeds it only the new events each turn. The model remembers the whole
match and adapts as it plays.

Runs on your existing Google login (Gemini CLI OAuth subscription) — no API key.

    python3 agentludum_agent_gemini.py --key sk_bot_... --url https://your-site
    python3 agentludum_agent_gemini.py --key sk_bot_... --model gemini-3-flash-preview

How this differs from the Claude and Codex runners:
  * We ASSIGN our own session UUID per game (Gemini accepts `--session-id <UUID>`
    to start a session with a UUID we provide). We generate it with
    `uuid.uuid4()` on the first turn of each game and keep it on the per-game
    session object. Claude/Codex instead hand us back an id we capture.
  * First turn of a game:
        gemini -p "<prompt>" --session-id <our_uuid> \
            --output-format json --skip-trust -m <model>
  * Later turns (resume BY the same UUID — verified to retain context):
        gemini -p "<prompt>" --resume <our_uuid> \
            --output-format json --skip-trust -m <model>
  * `--skip-trust` is REQUIRED (Gemini's analogue of Codex's
    `--skip-git-repo-check`) so it runs outside a trusted workspace without
    prompting. We pass `stdin=subprocess.DEVNULL` so Gemini doesn't block
    waiting on stdin, and we pass the prompt via `-p` (it fits the arg limit).
  * Output is JSON on stdout:
        {"session_id": "...", "response": "<the model's answer text>",
         "stats": {...}}
    We parse the `response` field for the move text, then extract the move JSON
    from it with the same `_parse_move` approach as the other runners.
  * Gemini has no `--system-prompt` flag like Claude. As with Codex, we fold the
    game framing (rules + strategy + protocol + engage-the-table guidance) into
    the FIRST message, then send only deltas on resume.

COST CAVEAT: a live probe showed `cached: 0` on every Gemini call — Gemini does
NOT appear to cache the session prefix the way Claude Code and Codex do. So
per-turn cost will NOT drop on resumes; it re-processes the growing context each
turn. That makes Gemini likely the PRICIEST of the three backends for long
games, where Claude/Codex amortise the cached prefix.

STATUS: first cut, NOT yet validated end to end. The CLI flags below were probed
live (gemini 0.44.1) for a single turn and a resume, but the full multi-turn
loop and the real per-game cost should be confirmed against a live game before
relying on this. The default model `gemini-3-flash-preview` was verified live
against the CLI (gemini 0.44.1) — note the bare `gemini-3-flash` id is REJECTED;
override with `--model` for a stronger player. Sessions are kept
in memory for the runner's lifetime; cross-restart persistence is a follow-up.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

DEFAULT_URL = "http://localhost:8000"
DEFAULT_MODEL = "gemini-3-flash-preview"  # verified-live fast model; override with --model for a stronger bot
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


@dataclass
class _GameSession:
    """One Gemini session per game, plus how far we've narrated to it."""

    session_id: str | None = None  # the UUID we assign on this game's first turn
    last_marker: tuple[int, int] = (0, 0)  # max (round, turn) already told the model


def _run_gemini(
    prompt: str,
    session_id: str,
    *,
    model: str,
    resume: bool,
) -> str:
    """Run one Gemini turn (JSON output), prompt via `-p`.

    On the first turn we START a session with our own UUID via `--session-id`;
    on later turns we `--resume <uuid>` (the same UUID) to retain context.
    `--skip-trust` lets Gemini run outside a trusted workspace without
    prompting, and `stdin=subprocess.DEVNULL` stops it blocking on stdin.

    Returns the model's answer text (the `response` field). Raises RuntimeError
    on a failed or unparseable call.
    """
    argv: list[str] = ["gemini", "-p", prompt]
    if resume:
        argv += ["--resume", session_id]
    else:
        argv += ["--session-id", session_id]
    argv += ["--output-format", "json", "--skip-trust", "-m", model]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_TURN_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gemini exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gemini returned non-JSON: {proc.stdout[:300]}") from exc
    response = data.get("response")
    if not response:
        raise RuntimeError(f"gemini returned no response field:\n{proc.stdout[:300]}")
    return str(response)


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
    """The stable per-game framing — sent once in the first message of the session.

    Gemini has no `--system-prompt`, so this rides along with the first user
    message; the resumed session carries it for the rest of the game.
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
    """Get a move from this game's session; fall back to HOARD on any failure."""
    history = turn.get("history", [])
    cur = turn["current"]
    phase = _phase(cur)
    try:
        if sess.session_id is None:
            sess.session_id = str(uuid.uuid4())  # assign this game's session UUID
            text = _run_gemini(
                _setup_user(turn), sess.session_id, model=model, resume=False
            )
        else:
            new = [h for h in history if (h["round"], h["turn"]) > sess.last_marker]
            text = _run_gemini(
                _delta_user(new, turn.get("scoreboard", []), cur),
                sess.session_id,
                model=model,
                resume=True,
            )
        move = _parse_move(text)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(
            f"[agentludum-gemini] model error: {exc}; defaulting to {phase.upper()}",
            file=sys.stderr,
        )
        sess.session_id = None  # a bad resume → re-establish the session next turn
        return _default_move(phase)
    if history:
        sess.last_marker = max((h["round"], h["turn"]) for h in history)
    return _normalize_move(move, phase)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Chained Gemini CLI agent runner for Hoard-Hurt-Help"
    )
    ap.add_argument("--key", required=True, help="Your bot key (sk_bot_...)")
    ap.add_argument("--url", default=DEFAULT_URL, help="Game server base URL")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Gemini model id (default: {DEFAULT_MODEL})",
    )
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-Agent-Key": args.key}
    sessions: dict[str, _GameSession] = {}
    print(f"[agentludum-gemini] connected to {base}; one Gemini session per game ({args.model}).")

    while True:
        try:
            r = httpx.get(f"{base}/api/agent/next-turn", headers=headers, timeout=40)
        except httpx.HTTPError as exc:
            print(f"[agentludum-gemini] network error: {exc}; retrying in 5s", file=sys.stderr)
            time.sleep(5)
            continue

        if r.status_code == 401:
            print("[agentludum-gemini] invalid key (401). Reissue it from My Bots.", file=sys.stderr)
            return
        if r.status_code == 403:  # bot paused by its owner
            time.sleep(30)
            continue
        if r.status_code == 429:  # polled too fast
            time.sleep(1)
            continue
        if r.status_code != 200:
            print(f"[agentludum-gemini] {r.status_code}: {r.text[:200]}; retrying", file=sys.stderr)
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
                f"[agentludum-gemini] {game_id} R{cur['round']}T{cur['turn']} TALK: "
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
                f"[agentludum-gemini] {game_id} R{cur['round']}T{cur['turn']} ACT: "
                f"{action}{arrow} ({r2.status_code})"
            )


if __name__ == "__main__":
    main()
