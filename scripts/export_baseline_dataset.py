#!/usr/bin/env python3
"""Export the baseline tournament DB to a CSV for model training.

Each row is one player-turn: the game state visible before the move,
the move made, and whether that player went on to win the match.

Run from the repo root:

    python scripts/export_baseline_dataset.py                       # default db
    python scripts/export_baseline_dataset.py --db data/baseline.sqlite
    python scripts/export_baseline_dataset.py --out data/baseline.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path


def _setup(db_path: str) -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")


async def export(db_path: str, out_path: str) -> int:
    _setup(db_path)

    from sqlalchemy import select
    from app.db import make_engine
    from app.models.match import Match, GameState
    from app.models.player import Player
    from app.models.agent import Agent
    from app.models.turn import Turn, TurnSubmission

    engine = make_engine(f"sqlite+aiosqlite:///{db_path}")
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as db:
        matches = (
            await db.execute(select(Match).where(Match.state == GameState.COMPLETED))
        ).scalars().all()
        if not matches:
            print("No completed matches found.")
            return 0

        match_ids = [m.id for m in matches]
        winner_by_match: dict[str, int | None] = {m.id: m.winner_player_id for m in matches}

        # strategy per player_id
        player_rows = (
            await db.execute(
                select(Player, Agent)
                .join(Agent, Agent.id == Player.agent_id)
                .where(Player.match_id.in_(match_ids))
            )
        ).all()
        player_strategy: dict[int, str] = {
            p.id: (a.bot_strategy or "unknown") for p, a in player_rows
        }
        player_match: dict[int, str] = {p.id: p.match_id for p, _ in player_rows}

        # all turns
        turns = (
            await db.execute(
                select(Turn).where(Turn.match_id.in_(match_ids))
            )
        ).scalars().all()
        turn_by_id: dict[int, Turn] = {t.id: t for t in turns}

        # all submissions (non-defaulted)
        all_player_ids = list(player_strategy.keys())
        submissions = (
            await db.execute(
                select(TurnSubmission)
                .where(
                    TurnSubmission.player_id.in_(all_player_ids),
                    TurnSubmission.was_defaulted.is_(False),
                )
                .order_by(
                    TurnSubmission.turn_id,
                    TurnSubmission.player_id,
                )
            )
        ).scalars().all()

        # scoreboard-before: for each (turn_id, player_id) we want the
        # round_score_after from the *previous* turn in the same round.
        # Build a lookup: (match_id, round, turn_num, player_id) -> round_score_after
        score_lookup: dict[tuple[str, int, int, int], int] = {}
        for sub in submissions:
            turn = turn_by_id.get(sub.turn_id)
            if turn is None:
                continue
            mid = player_match.get(sub.player_id, "")
            score_lookup[(mid, turn.round, turn.turn, sub.player_id)] = sub.round_score_after

        rows_written = 0
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        with open(out, "w", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "match_id",
                    "round",
                    "turn",
                    "player_id",
                    "strategy",
                    "score_before",
                    "action",
                    "target_strategy",
                    "points_delta",
                    "round_score_after",
                    "match_won",
                ],
            )
            writer.writeheader()

            for sub in submissions:
                turn = turn_by_id.get(sub.turn_id)
                if turn is None:
                    continue
                mid = player_match.get(sub.player_id, "")
                strategy = player_strategy.get(sub.player_id, "unknown")
                target_strategy = player_strategy.get(sub.target_player_id, "") if sub.target_player_id else ""

                # score before this turn = round_score_after of the previous turn
                prev_turn = turn.turn - 1
                if prev_turn > 0:
                    score_before = score_lookup.get((mid, turn.round, prev_turn, sub.player_id), 0)
                else:
                    score_before = 0

                winner_pid = winner_by_match.get(mid)
                match_won = 1 if winner_pid == sub.player_id else 0

                writer.writerow({
                    "match_id": mid,
                    "round": turn.round,
                    "turn": turn.turn,
                    "player_id": sub.player_id,
                    "strategy": strategy,
                    "score_before": score_before,
                    "action": sub.action,
                    "target_strategy": target_strategy,
                    "points_delta": sub.points_delta,
                    "round_score_after": sub.round_score_after,
                    "match_won": match_won,
                })
                rows_written += 1

    await engine.dispose()
    return rows_written


def main() -> None:
    ap = argparse.ArgumentParser(description="Export baseline tournament to CSV")
    ap.add_argument("--db", default="data/baseline.sqlite", help="SQLite input path")
    ap.add_argument("--out", default="data/baseline.csv", help="CSV output path")
    args = ap.parse_args()

    rows = asyncio.run(export(args.db, args.out))
    print(f"Wrote {rows} rows to {args.out}")


if __name__ == "__main__":
    main()
