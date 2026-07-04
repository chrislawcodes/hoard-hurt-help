#!/usr/bin/env python3
"""Spin up a fast local test match and fill it with built-in bots.

Creates a short match directly in the DB and seats N built-in bots — the house
"scripted opponents" the server plays itself, with no external runner. With the
server running, its auto-start poller starts the match once scheduled_start
passes and at least three players are seated; the bots then play automatically.

(For a fake *agent* that plays over the public agent API instead, see
scripts/random_agent.py — a different tool, for testing the agent play path.)

Run from the repo root, with the server already running:
    python scripts/new_test_game.py                 # 3 bots, auto-starts + plays
    python scripts/new_test_game.py --bots 6         # a fuller table
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from offline_db import ensure_repo_root_on_path  # noqa: E402

# Make `app` importable when run as `python scripts/new_test_game.py`. Note:
# unlike the other three scripts here, this one does NOT set DATABASE_URL or
# create the schema — it targets the already-running server's real dev DB
# (see module docstring), so it only needs the sys.path half of the bootstrap.
ensure_repo_root_on_path()


async def _create_match(
    name: str, deadline: int, rounds: int, turns: int, start_in: int, bots: int
) -> tuple[str, int]:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.engine.bot_presets import allocate_default_bot_names, bot_presets
    from app.engine.bots.seating import add_bots_to_game
    from app.engine.tokens import generate_match_id
    from app.models.match import GameState, Match

    async with SessionLocal() as db:
        existing = (await db.execute(select(Match.id))).scalars().all()
        n = max((int(x[2:]) for x in existing if x.startswith("M_") and x[2:].isdigit()), default=0) + 1
        mid = generate_match_id(n)
        match = Match(
            id=mid,
            name=name,
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(seconds=start_in),
            per_turn_deadline_seconds=deadline,
            total_rounds=rounds,
            turns_per_round=turns,
        )
        db.add(match)
        await db.flush()

        # Seat built-in bots. There are 9 personality presets; cycle them when
        # asked for more, and cap at the match's player limit. add_bots_to_game
        # creates the bot agents, validates each profile, and commits.
        presets = bot_presets()
        bots = max(1, min(bots, match.max_players))
        names = allocate_default_bot_names(bots)
        seats = [(names[i], presets[i % len(presets)].id) for i in range(bots)]
        seated = await add_bots_to_game(db, match, seats)
        return mid, len(seated)


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a fast test match and fill it with built-in bots")
    ap.add_argument("--bots", type=int, default=3, help="How many built-in bots to seat (3+ to auto-start)")
    ap.add_argument("--deadline", type=int, default=5, help="Per-turn deadline seconds")
    ap.add_argument("--rounds", type=int, default=2, help="Rounds")
    ap.add_argument("--turns", type=int, default=3, help="Turns per round")
    ap.add_argument("--start-in", type=int, default=12, help="Seconds until the match starts")
    ap.add_argument("--url", default="http://localhost:8000", help="Server base URL (for the viewer link)")
    ap.add_argument("--name", default="Test match", help="Match name")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    mid, seated = asyncio.run(
        _create_match(args.name, args.deadline, args.rounds, args.turns, args.start_in, args.bots)
    )
    print(f"Created {mid}: {args.rounds}x{args.turns} turns @ {args.deadline}s, starts in ~{args.start_in}s")
    print(f"Seated {seated} built-in bot(s) — the server plays them automatically.")
    print(f"Viewer:  {base}/games/hoard-hurt-help/matches/{mid}")
    if seated < 3:
        print(
            f"NOTE: only {seated} bot(s). A match needs 3 players or it is cancelled at start —"
            " seat at least 3 with --bots."
        )


if __name__ == "__main__":
    main()
