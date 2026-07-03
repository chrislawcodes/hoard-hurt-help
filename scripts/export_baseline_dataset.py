#!/usr/bin/env python3
"""Export the baseline tournament DB to a CSV for model training.

Each row is one player-turn: the full game state visible before the move,
the move made, and whether that player went on to win the match.

Columns
-------
match_id, round, turn, player_id, strategy
n_players, total_rounds, turns_per_round
score_before, round_wins_before
score_rank, score_gap_to_leader, score_mean, score_std
round_wins_rank, round_wins_leader, round_wins_mean
action, target_strategy
points_delta, round_score_after
match_won

Run from the repo root:

    python scripts/export_baseline_dataset.py                       # default db
    python scripts/export_baseline_dataset.py --db data/baseline.sqlite
    python scripts/export_baseline_dataset.py --out data/baseline.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from offline_db import ensure_repo_root_on_path, set_database_url  # noqa: E402


def _setup(db_path: str) -> None:
    ensure_repo_root_on_path()
    # setdefault, not override: this script only reads a DB another script
    # (baseline_tournament.py) already created and must not clobber a
    # caller's own DATABASE_URL. No mkdir/schema-create either — the file and
    # its schema are expected to exist already.
    set_database_url(db_path, mkdir=False, override=False)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


async def export(db_path: str, out_path: str) -> int:
    _setup(db_path)

    from sqlalchemy import select, text
    from app.db import make_engine
    from app.models.player import Player
    from app.models.agent import Agent
    from app.models.turn import Turn, TurnSubmission

    engine = make_engine(f"sqlite+aiosqlite:///{db_path}")
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Named tuple stand-in so the rest of the code can use m.id, m.total_rounds etc.
    from typing import NamedTuple

    class MatchRow(NamedTuple):
        id: str
        total_rounds: int
        turns_per_round: int
        winner_player_id: int | None

    async with factory() as db:
        raw_matches = (
            await db.execute(
                text(
                    "SELECT id, total_rounds, turns_per_round, winner_player_id"
                    " FROM matches WHERE state = 'completed'"
                )
            )
        ).fetchall()
        if not raw_matches:
            print("No completed matches found.")
            return 0

        matches = [MatchRow(*r) for r in raw_matches]
        match_ids = [m.id for m in matches]
        match_by_id: dict[str, MatchRow] = {m.id: m for m in matches}
        winner_by_match: dict[str, int | None] = {m.id: m.winner_player_id for m in matches}

        # strategy + match per player_id
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

        # player ids per match (for n_players and per-turn scoreboard)
        players_by_match: dict[str, list[int]] = {}
        for p, _ in player_rows:
            players_by_match.setdefault(p.match_id, []).append(p.id)

        # all turns
        turns = (
            await db.execute(
                select(Turn).where(Turn.match_id.in_(match_ids))
            )
        ).scalars().all()
        turn_by_id: dict[int, Turn] = {t.id: t for t in turns}

        # last turn number per (match_id, round) — used for round-win reconstruction
        last_turn_of_round: dict[tuple[str, int], int] = {}
        for t in turns:
            key = (t.match_id, t.round)
            last_turn_of_round[key] = max(last_turn_of_round.get(key, 0), t.turn)

        # turn_id lookup: (match_id, round, turn_num) -> turn_id
        turn_id_lookup: dict[tuple[str, int, int], int] = {}
        for t in turns:
            turn_id_lookup[(t.match_id, t.round, t.turn)] = t.id

        # ALL submissions (including defaulted) for scoreboard reconstruction
        all_player_ids = list(player_strategy.keys())
        all_submissions = (
            await db.execute(
                select(TurnSubmission)
                .where(TurnSubmission.player_id.in_(all_player_ids))
                .order_by(TurnSubmission.turn_id, TurnSubmission.player_id)
            )
        ).scalars().all()

        # score_lookup: (match_id, round, turn_num, player_id) -> round_score_after
        score_lookup: dict[tuple[str, int, int, int], int] = {}
        for sub in all_submissions:
            turn = turn_by_id.get(sub.turn_id)
            if turn is None:
                continue
            mid = player_match.get(sub.player_id, "")
            score_lookup[(mid, turn.round, turn.turn, sub.player_id)] = sub.round_score_after

        # ---------------------------------------------------------------------------
        # Round-win reconstruction.
        # For each (match_id, round): find the last turn, take the highest
        # round_score_after among all players, award 1/n to each tied winner.
        # round_wins_before[match_id][player_id][round] = cumulative wins entering
        # round R (i.e. sum of wins from rounds 1..R-1).
        # ---------------------------------------------------------------------------

        # wins_in_round[match_id][round][player_id] = wins awarded
        wins_in_round: dict[str, dict[int, dict[int, float]]] = {}
        for (mid, rnd), last_t in last_turn_of_round.items():
            last_tid = turn_id_lookup.get((mid, rnd, last_t))
            if last_tid is None:
                continue
            final_subs = [
                s for s in all_submissions
                if s.turn_id == last_tid
            ]
            if not final_subs:
                continue
            max_score = max(s.round_score_after for s in final_subs)
            winners = [s for s in final_subs if s.round_score_after == max_score]
            share = 1.0 / len(winners)
            wins_in_round.setdefault(mid, {}).setdefault(rnd, {})
            for s in final_subs:
                wins_in_round[mid][rnd][s.player_id] = share if s in winners else 0.0

        def _round_wins_before(mid: str, pid: int, rnd: int) -> float:
            total = 0.0
            for r in range(1, rnd):
                total += wins_in_round.get(mid, {}).get(r, {}).get(pid, 0.0)
            return total

        # non-defaulted submissions are the focal-player rows we emit
        submissions = [s for s in all_submissions if not s.was_defaulted]

    rows_written = 0
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "match_id",
        "round",
        "turn",
        "player_id",
        "strategy",
        # match config
        "n_players",
        "total_rounds",
        "turns_per_round",
        # focal player state
        "score_before",
        "round_wins_before",
        # scoreboard context (all players, before this turn)
        "score_rank",
        "score_gap_to_leader",
        "score_mean",
        "score_std",
        "round_wins_rank",
        "round_wins_leader",
        "round_wins_mean",
        # action
        "action",
        "target_player_id",
        "target_strategy",
        # outcomes
        "points_delta",
        "round_score_after",
        # label
        "match_won",
    ]

    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for sub in submissions:
            turn = turn_by_id.get(sub.turn_id)
            if turn is None:
                continue

            mid = player_match.get(sub.player_id, "")
            match = match_by_id.get(mid)
            if match is None:
                continue

            strategy = player_strategy.get(sub.player_id, "unknown")
            target_pid = sub.target_player_id if sub.target_player_id else ""
            target_strategy = (
                player_strategy.get(sub.target_player_id, "")
                if sub.target_player_id
                else ""
            )

            rnd = turn.round
            t_num = turn.turn
            pid = sub.player_id

            # --- focal player score before this turn ---
            if t_num > 1:
                score_before = score_lookup.get((mid, rnd, t_num - 1, pid), 0)
            else:
                score_before = 0

            # --- round wins entering this round ---
            rwins_before = _round_wins_before(mid, pid, rnd)

            # --- full scoreboard before this turn ---
            all_pids = players_by_match.get(mid, [])
            if t_num > 1:
                all_scores = [
                    score_lookup.get((mid, rnd, t_num - 1, p), 0) for p in all_pids
                ]
            else:
                all_scores = [0] * len(all_pids)

            all_rwins = [_round_wins_before(mid, p, rnd) for p in all_pids]

            score_leader = max(all_scores) if all_scores else 0
            score_rank = (
                sum(1 for s in all_scores if s > score_before) + 1
            )
            score_gap = score_leader - score_before

            rwins_leader = max(all_rwins) if all_rwins else 0.0
            rwins_rank = (
                sum(1 for w in all_rwins if w > rwins_before) + 1
            )

            winner_pid = winner_by_match.get(mid)
            match_won = 1 if winner_pid == pid else 0

            writer.writerow({
                "match_id": mid,
                "round": rnd,
                "turn": t_num,
                "player_id": pid,
                "strategy": strategy,
                "n_players": len(all_pids),
                "total_rounds": match.total_rounds,
                "turns_per_round": match.turns_per_round,
                "score_before": score_before,
                "round_wins_before": round(rwins_before, 3),
                "score_rank": score_rank,
                "score_gap_to_leader": score_gap,
                "score_mean": round(_mean([float(s) for s in all_scores]), 2),
                "score_std": round(_std([float(s) for s in all_scores]), 2),
                "round_wins_rank": rwins_rank,
                "round_wins_leader": round(rwins_leader, 3),
                "round_wins_mean": round(_mean(all_rwins), 3),
                "action": sub.action,
                "target_player_id": target_pid,
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
