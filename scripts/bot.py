#!/usr/bin/env python3
"""A throwaway test bot: joins a Hoard-Hurt-Help game and plays a random strategy.

Fills a slot so you can run live games without standing up real AI clients.
Polls at ~1 Hz (the server rate-limits faster polling), reads each turn, and
submits HOARD / HELP / HURT until the game ends.

Usage:
    python scripts/bot.py --game G_0001
    python scripts/bot.py --game G_0001 --name BOT_2 --url http://localhost:8000
"""

import argparse
import random
import sys
import time

import httpx


def _join(base: str, game: str, name: str) -> tuple[str, str]:
    r = httpx.post(
        f"{base}/api/games/{game}/join",
        json={
            "display_name": name,
            "strategy_prompt": "Random test bot.",
            "model_self_report": "test-bot",
        },
        timeout=10,
    )
    if r.status_code != 201:
        print(f"[{name}] join failed: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    data = r.json()
    return data["agent_id"], data["agent_key"]


def _play_turn(base: str, game: str, name: str, headers: dict, body: dict) -> None:
    static, dynamic = body["static"], body["dynamic"]
    others = [a for a in static["all_agent_ids"] if a != static["your_agent_id"]]
    action = random.choice(["HOARD", "HELP", "HURT"])
    target = None
    if action in ("HELP", "HURT"):
        if others:
            target = random.choice(others)
        else:
            action = "HOARD"  # nobody to target
    r = httpx.post(
        f"{base}/api/games/{game}/submit",
        headers=headers,
        json={
            "turn_token": dynamic["turn_token"],
            "action": action,
            "target_id": target,
            "message": f"{name}: {action}",
        },
        timeout=10,
    )
    arrow = f" -> {target}" if target else ""
    print(f"[{name}] R{dynamic['current_round']}T{dynamic['current_turn']}: {action}{arrow} ({r.status_code})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hoard-Hurt-Help test bot")
    ap.add_argument("--game", required=True, help="Game id, e.g. G_0001")
    ap.add_argument("--name", default=None, help="Display name (default: BOT_<random>)")
    ap.add_argument("--url", default="http://localhost:8000", help="Server base URL")
    ap.add_argument("--poll", type=float, default=1.2, help="Seconds between polls (>1 to respect rate limit)")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    name = args.name or f"BOT_{random.randint(1000, 9999)}"
    name, key = _join(base, args.game, name)
    headers = {"X-Agent-Key": key}
    print(f"[{name}] joined {args.game}; waiting for the game to start...")

    while True:
        time.sleep(args.poll)
        try:
            r = httpx.get(f"{base}/api/games/{args.game}/turn", headers=headers, timeout=10)
        except httpx.HTTPError as e:
            print(f"[{name}] poll error: {e}", file=sys.stderr)
            continue
        if r.status_code == 429:  # polled too fast — back off and retry
            continue
        if r.status_code != 200:
            print(f"[{name}] poll {r.status_code}: {r.text}", file=sys.stderr)
            continue

        body = r.json()
        status = body.get("status")
        if status == "your_turn":
            _play_turn(base, args.game, name, headers, body)
        elif status == "game_completed":
            print(f"[{name}] game completed. bye.")
            return
        elif status == "waiting" and (
            body.get("reason") == "game_over"
            or body.get("game_state") in ("completed", "cancelled")
        ):
            print(f"[{name}] game over ({body.get('game_state')}). bye.")
            return
        # otherwise: not started / not your turn / already submitted — keep polling


if __name__ == "__main__":
    main()
