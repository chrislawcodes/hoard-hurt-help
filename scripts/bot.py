#!/usr/bin/env python3
"""A throwaway random test bot for Hoard-Hurt-Help.

Fills a seat with random HOARD / HELP / HURT moves so you can run live games
without standing up real AI runners. Unlike scripts/agentludum_connector.py it makes no
model calls — it just plays randomly.

Setup (new bot model): create a bot and enter it into a game on the site, then
run this with that bot's key. It plays every game the bot is in via
get_next_turn — no joining over the API.

Usage:
    python scripts/bot.py --key sk_bot_... --url http://localhost:8000
"""

import argparse
import random
import sys
import time

import httpx


def _phase(cur: dict) -> str:
    return str(cur.get("phase", "act")).lower()


def main() -> None:
    ap = argparse.ArgumentParser(description="Hoard-Hurt-Help random test bot")
    ap.add_argument("--key", required=True, help="Bot key (sk_bot_...)")
    ap.add_argument("--url", default="http://localhost:8000", help="Server base URL")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-Agent-Key": args.key}
    print(f"[bot] connected to {base}; playing every game this bot is in.")

    while True:
        try:
            r = httpx.get(f"{base}/api/agent/next-turn", headers=headers, timeout=40)
        except httpx.HTTPError as e:
            print(f"[bot] network error: {e}", file=sys.stderr)
            time.sleep(5)
            continue
        if r.status_code == 401:
            print("[bot] invalid key (401). Reissue from My Bots.", file=sys.stderr)
            return
        if r.status_code in (403, 429):  # paused, or polled too fast
            time.sleep(5)
            continue
        if r.status_code != 200:
            print(f"[bot] {r.status_code}: {r.text[:200]}", file=sys.stderr)
            time.sleep(5)
            continue

        turn = r.json()
        if turn.get("status") != "your_turn":
            time.sleep(turn.get("next_poll_after_seconds", 5))
            continue

        game_id = turn["game_id"]
        static, current = turn["static"], turn["current"]
        phase = _phase(current)
        others = [a for a in static["all_agent_ids"] if a != static["your_agent_id"]]
        if phase == "talk":
            canned = random.choice(
                [
                    "keeping an eye on the table",
                    "let's see what sticks",
                    "noted",
                    "I am watching",
                ]
            )
            r2 = httpx.post(
                f"{base}/api/games/{game_id}/message",
                headers=headers,
                json={
                    "turn_token": current["turn_token"],
                    "message": f"{static['your_agent_id']}: {canned}",
                    "thinking": "",
                },
                timeout=20,
            )
            print(
                f"[bot] {game_id} R{current['round']}T{current['turn']} TALK: "
                f"({r2.status_code})"
            )
        else:
            action = random.choice(["HOARD", "HELP", "HURT"])
            target = None
            if action in ("HELP", "HURT"):
                if others:
                    target = random.choice(others)
                else:
                    action = "HOARD"  # nobody to target
            r2 = httpx.post(
                f"{base}/api/games/{game_id}/submit",
                headers=headers,
                json={
                    "turn_token": current["turn_token"],
                    "action": action,
                    "target_id": target,
                    "thinking": "",
                },
                timeout=20,
            )
            arrow = f" -> {target}" if target else ""
            print(
                f"[bot] {game_id} R{current['round']}T{current['turn']} ACT: "
                f"{action}{arrow} ({r2.status_code})"
            )


if __name__ == "__main__":
    main()
