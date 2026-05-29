#!/usr/bin/env python3
"""Spin up a fast local test game and fill it with bots.

Creates a short game (small rounds/turns + low deadline) directly in the DB,
launches N bots that join over HTTP, and prints the viewer URL. The running
server's auto-start poller starts the game once scheduled_start passes and at
least 3 players have joined; resolve-early then runs each turn in ~a second.

Run from the repo root, with the server already running:
    python scripts/new_test_game.py                 # 3 bots, full auto run
    python scripts/new_test_game.py --bots 2         # leave a slot for your AI
"""

import argparse
import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone


async def _create_game(name, deadline, rounds, turns, start_in) -> str:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.engine.tokens import generate_game_id
    from app.models.game import Game, GameState

    async with SessionLocal() as db:
        existing = (await db.execute(select(Game.id))).scalars().all()
        n = max((int(x.split("_")[1]) for x in existing if x.startswith("G_")), default=0) + 1
        gid = generate_game_id(n)
        db.add(
            Game(
                id=gid,
                name=name,
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(seconds=start_in),
                min_players=3,
                max_players=100,
                per_turn_deadline_seconds=deadline,
                total_rounds=rounds,
                turns_per_round=turns,
            )
        )
        await db.commit()
    return gid


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a fast test game and fill it with bots")
    ap.add_argument("--bots", type=int, default=3, help="How many bots to launch")
    ap.add_argument("--deadline", type=int, default=5, help="Per-turn deadline seconds")
    ap.add_argument("--rounds", type=int, default=2, help="Rounds")
    ap.add_argument("--turns", type=int, default=3, help="Turns per round")
    ap.add_argument("--start-in", type=int, default=12, help="Seconds until the game starts")
    ap.add_argument("--url", default="http://localhost:8000", help="Server base URL")
    ap.add_argument("--name", default="Test game", help="Game name")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    gid = asyncio.run(
        _create_game(args.name, args.deadline, args.rounds, args.turns, args.start_in)
    )
    print(f"Created {gid}: {args.rounds}x{args.turns} turns @ {args.deadline}s, starts in ~{args.start_in}s")
    print(f"Viewer:  {base}/games/{gid}")
    if args.bots < 3:
        print(
            f"NOTE: only {args.bots} bot(s). A game needs 3 players or it is cancelled at start —"
            f" join the rest at {base}/games/{gid}/join before then."
        )

    procs = []
    for i in range(args.bots):
        procs.append(
            subprocess.Popen(
                [sys.executable, "scripts/bot.py", "--game", gid, "--name", f"BOT_{i + 1}", "--url", base]
            )
        )
    print(f"Launched {args.bots} bot(s). Watch {base}/games/{gid} — Ctrl-C to stop.")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
