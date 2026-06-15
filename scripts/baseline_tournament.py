#!/usr/bin/env python3
"""Run baseline bot-only tournaments and write per-turn logs to a local DB.

Each match seats 10 bots sampled with replacement from the 9 strategy pool.
Matches are grouped into batches of 25 so you can see when summary stats
stabilise across batches.

Run from the repo root (no server needed):

    python scripts/baseline_tournament.py                   # 1 batch of 25
    python scripts/baseline_tournament.py --batches 4       # 100 matches
    python scripts/baseline_tournament.py --batches 4 --seed 99
    python scripts/baseline_tournament.py --db data/baseline.sqlite

The script writes to a local SQLite file (default: data/baseline.sqlite) that
is completely separate from the live app database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: point the app at the baseline DB before any app imports.
# ---------------------------------------------------------------------------

def _setup_db_url(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"


# ---------------------------------------------------------------------------
# App imports (after env is set).
# ---------------------------------------------------------------------------

def _import_app() -> None:
    # Ensure repo root is on sys.path so `app.*` resolves.
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


# ---------------------------------------------------------------------------
# Tournament logic
# ---------------------------------------------------------------------------

STRATEGIES: tuple[str, ...] = (
    "coalition_seeker",
    "loyal_partner",
    "grudger",
    "leader_pressure",
    "opportunist",
    "endgame_sniper",
    "diplomat",
    "crowd_follower",
    "coin_flip",
)

PLAYERS_PER_MATCH = 10
BATCH_SIZE = 25


async def _ensure_schema() -> None:
    from app.db import engine
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Serializes match creation: match IDs derive from a row count, so two
# concurrent creators would mint the same ID and hit the primary-key constraint.
_CREATE_LOCK = asyncio.Lock()


async def _run_one_match(match_index: int, strategies: list[str]) -> str:
    """Create one match, seat the given 10 bots, run it, return match id."""
    from app.db import SessionLocal
    from app.engine.bots.seating import add_bots_to_game
    from app.engine.state_machine import assert_transition
    from app.engine.tokens import generate_match_id
    from app.models.match import GameState, Match

    async with _CREATE_LOCK:
        async with SessionLocal() as db:
            from sqlalchemy import select, func
            existing_count = await db.scalar(select(func.count()).select_from(Match)) or 0
            match_id = generate_match_id(existing_count + 1)

            now = datetime.now(timezone.utc)
            match = Match(
                id=match_id,
                name=f"baseline-{match_index}",
                game="hoard-hurt-help",
                state=GameState.REGISTERING,
                scheduled_start=now - timedelta(seconds=1),
                per_turn_deadline_seconds=0,  # resolve immediately once all bots submit
                total_rounds=7,
                turns_per_round=7,
                min_players=3,
                max_players=100,
            )
            db.add(match)
            await db.flush()

            # Build unique seat names: include strategy + index so same-strategy
            # bots at the same table are distinguishable. Seat names allow only
            # letters, digits, and spaces (BOT_AGENT_NAME_RE), so drop underscores.
            seats: list[tuple[str, str]] = []
            strategy_counts: dict[str, int] = {}
            for strategy in strategies:
                n = strategy_counts.get(strategy, 0) + 1
                strategy_counts[strategy] = n
                seat_name = f"{strategy.replace('_', ' ')[:28]} {n}"
                seats.append((seat_name, strategy))

            await add_bots_to_game(db, match, seats)

            # Transition to ACTIVE directly instead of scheduler.start_game():
            # start_game also spawns the registry's own _run_game task, and we
            # drive the loop ourselves below — two loops on one match would race.
            assert_transition(match.state, GameState.ACTIVE)
            match.state = GameState.ACTIVE
            match.started_at = now
            await db.commit()

    from app.engine.scheduler import _run_game
    await _run_game(match_id)
    return match_id


async def _batch_summary(match_ids: list[str]) -> dict[str, dict[str, float]]:
    """Return per-strategy win-rate, avg points/turn, action breakdown."""
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models.player import Player
    from app.models.agent import Agent
    from app.models.match import Match
    from app.models.turn import TurnSubmission

    stats: dict[str, dict[str, float]] = {}

    async with SessionLocal() as db:
        # Load all players + their agents for these matches
        rows = (
            await db.execute(
                select(Player, Agent)
                .join(Agent, Agent.id == Player.agent_id)
                .where(Player.match_id.in_(match_ids))
            )
        ).all()

        # Load winner_player_id for each match
        matches = (
            await db.execute(select(Match).where(Match.id.in_(match_ids)))
        ).scalars().all()
        winner_ids = {m.winner_player_id for m in matches if m.winner_player_id}

        # Load all submissions for these matches
        player_ids = [p.id for p, _ in rows]
        if not player_ids:
            return {}

        submissions = (
            await db.execute(
                select(TurnSubmission)
                .where(
                    TurnSubmission.player_id.in_(player_ids),
                    TurnSubmission.was_defaulted.is_(False),
                )
            )
        ).scalars().all()

    # Aggregate by strategy
    by_strategy: dict[str, dict] = {}
    player_strategy: dict[int, str] = {}
    for player, agent in rows:
        strategy = agent.bot_strategy or "unknown"
        player_strategy[player.id] = strategy
        if strategy not in by_strategy:
            by_strategy[strategy] = {
                "appearances": 0,
                "wins": 0,
                "total_points": 0,
                "turns": 0,
                "HOARD": 0,
                "HELP": 0,
                "HURT": 0,
            }
        by_strategy[strategy]["appearances"] += 1
        if player.id in winner_ids:
            by_strategy[strategy]["wins"] += 1

    for sub in submissions:
        strategy = player_strategy.get(sub.player_id, "unknown")
        if strategy not in by_strategy:
            continue
        by_strategy[strategy]["total_points"] += sub.points_delta
        by_strategy[strategy]["turns"] += 1
        if sub.action in ("HOARD", "HELP", "HURT"):
            by_strategy[strategy][sub.action] += 1

    for strategy, d in by_strategy.items():
        appearances = d["appearances"] or 1
        turns = d["turns"] or 1
        stats[strategy] = {
            "appearances": d["appearances"],
            "win_rate": round(d["wins"] / appearances, 3),
            "avg_pts_per_turn": round(d["total_points"] / turns, 2),
            "hoard_pct": round(d["HOARD"] / turns, 3),
            "help_pct": round(d["HELP"] / turns, 3),
            "hurt_pct": round(d["HURT"] / turns, 3),
        }

    return stats


def _print_summary(batch_num: int, cumulative_ids: list[str], stats: dict) -> None:
    print(f"\n{'='*72}")
    print(f"  Batch {batch_num} complete  |  {len(cumulative_ids)} matches total")
    print(f"{'='*72}")
    header = f"{'Strategy':<20} {'App':>6} {'WinR':>6} {'Pts/T':>7} {'Hrd%':>6} {'Hlp%':>6} {'Hrt%':>6}"
    print(header)
    print("-" * 72)
    for strategy in sorted(stats, key=lambda s: -stats[s]["win_rate"]):
        d = stats[strategy]
        print(
            f"{strategy:<20} {d['appearances']:>6} {d['win_rate']:>6.3f} "
            f"{d['avg_pts_per_turn']:>7.2f} {d['hoard_pct']:>6.3f} "
            f"{d['help_pct']:>6.3f} {d['hurt_pct']:>6.3f}"
        )


async def run_tournament(
    *,
    batches: int,
    seed: int,
    db_path: str,
    concurrency: int,
) -> None:
    _setup_db_url(db_path)
    _import_app()

    await _ensure_schema()

    rng = random.Random(seed)
    all_match_ids: list[str] = []
    match_index = 0

    for batch_num in range(1, batches + 1):
        batch_ids: list[str] = []
        sem = asyncio.Semaphore(concurrency)

        # Pre-draw every roster for the batch on the main coroutine so the
        # master seed fully determines them, independent of task interleaving.
        rosters = [
            [rng.choice(STRATEGIES) for _ in range(PLAYERS_PER_MATCH)]
            for _ in range(BATCH_SIZE)
        ]

        async def _run_guarded(idx: int, strategies: list[str]) -> str:
            async with sem:
                return await _run_one_match(idx, strategies)

        tasks = [
            asyncio.create_task(_run_guarded(match_index + i, rosters[i]))
            for i in range(BATCH_SIZE)
        ]
        match_index += BATCH_SIZE

        results = await asyncio.gather(*tasks)
        batch_ids.extend(results)
        all_match_ids.extend(batch_ids)

        stats = await _batch_summary(all_match_ids)
        _print_summary(batch_num, all_match_ids, stats)

    print(f"\nDone. {len(all_match_ids)} matches written to {db_path}")
    print("Export with: python scripts/export_baseline_dataset.py --db", db_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run baseline bot tournament")
    ap.add_argument("--batches", type=int, default=1, help="Number of 25-match batches")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    ap.add_argument("--db", default="data/baseline.sqlite", help="SQLite output path")
    ap.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max matches running in parallel (default 4)",
    )
    args = ap.parse_args()

    asyncio.run(
        run_tournament(
            batches=args.batches,
            seed=args.seed,
            db_path=args.db,
            concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    main()
