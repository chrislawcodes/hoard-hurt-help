#!/usr/bin/env python3
"""Compute derived features from baseline.csv → baseline_features.csv.

Reads the raw export CSV (one row per player-turn) and enriches it with
17 additional columns derived from behavioral history, cross-player turn
dynamics, momentum, table aggression, end-game pressure, and leader
stability.

New columns
-----------
Behavioral history (focal player's prior non-defaulted actions this match):
  help_count            cumulative HELP actions before this turn
  hurt_count            cumulative HURT actions before this turn
  hoard_count           cumulative HOARD actions before this turn
  times_targeted        times HURTed by any other player before this turn

Turn-level social dynamics (all players' actions this turn):
  table_help_count      HELP actions taken this turn
  table_hurt_count      HURT actions taken this turn
  table_hoard_count     HOARD actions taken this turn
  was_piled_on          1 if 2+ players HURTed this player this turn
  pile_on_max           max times any single player was HURTed this turn
  got_mutual_help       1 if this player HELPed someone who HELPed them back

Momentum:
  consecutive_round_wins  consecutive rounds won entering this round (0 if none)
  last_points_delta       points_delta from the previous turn (0 on first turn)

Table dynamics (all match actions before this turn):
  match_help_rate       fraction of prior match actions that were HELP
  match_hurt_rate       fraction of prior match actions that were HURT

End-game pressure:
  self_can_clinch       1 if winning this round gives self > half of total_rounds wins
  leader_can_clinch     1 if the round_wins leader (not self) can clinch this round

Leader stability:
  rounds_same_leader    consecutive rounds the same player has led the round_wins standings

Run:
    python scripts/compute_features.py
    python scripts/compute_features.py --csv data/baseline.csv --out data/baseline_features.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

# `app` is an installed package — run this script via the project venv.
from app.engine.win_prob_features import DERIVED_FEATURE_COLUMNS

# This script is the producer of the derived CSV columns; the trainers and the
# live engine consume them. All three share one column vocabulary, defined in
# app/engine/win_prob_features.py.
NEW_COLUMNS: list[str] = list(DERIVED_FEATURE_COLUMNS)


def compute(csv_path: str, out_path: str) -> int:
    # ------------------------------------------------------------------
    # Pass 1: load all rows and build index structures.
    # ------------------------------------------------------------------
    print("Pass 1: loading rows …", end=" ", flush=True)
    all_rows: list[dict[str, str]] = []
    with open(csv_path, newline="") as fh:
        all_rows = list(csv.DictReader(fh))
    print(f"{len(all_rows):,} rows")

    # by_turn[(mid, rnd, turn)] -> list of row dicts for ALL players that turn
    by_turn: dict[tuple[str, int, int], list[dict[str, str]]] = defaultdict(list)
    # by_player[(mid, pid)] -> list of row dicts, will be sorted (round, turn)
    by_player: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)

    for row in all_rows:
        mid = row["match_id"]
        rnd, turn, pid = int(row["round"]), int(row["turn"]), int(row["player_id"])
        by_turn[(mid, rnd, turn)].append(row)
        by_player[(mid, pid)].append(row)

    for rows in by_player.values():
        rows.sort(key=lambda r: (int(r["round"]), int(r["turn"])))

    # ------------------------------------------------------------------
    # Pass 2: derive round_won per (mid, rnd, pid).
    # Winner = player(s) with highest round_score_after on the last turn.
    # ------------------------------------------------------------------
    print("Pass 2: deriving round winners …", end=" ", flush=True)
    last_turn_of_round: dict[tuple[str, int], int] = {}
    for mid, rnd, turn in by_turn:
        key = (mid, rnd)
        last_turn_of_round[key] = max(last_turn_of_round.get(key, 0), turn)

    round_won: dict[tuple[str, int, int], bool] = {}
    for (mid, rnd), last_t in last_turn_of_round.items():
        final_rows = by_turn.get((mid, rnd, last_t), [])
        if not final_rows:
            continue
        best = max(int(r["round_score_after"]) for r in final_rows)
        for r in final_rows:
            round_won[(mid, rnd, int(r["player_id"]))] = (
                int(r["round_score_after"]) == best
            )
    print(f"{len(round_won):,} (match, round, player) entries")

    # ------------------------------------------------------------------
    # Pass 3: precompute targeting events (HURT actions) per (mid, target_pid).
    # ------------------------------------------------------------------
    print("Pass 3: indexing targeting events …", end=" ", flush=True)
    # (mid, target_pid) -> sorted list of (rnd, turn)
    hurt_events: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    for row in all_rows:
        if row["action"] == "HURT" and row["target_player_id"]:
            mid = row["match_id"]
            target_pid = int(row["target_player_id"])
            hurt_events[(mid, target_pid)].append(
                (int(row["round"]), int(row["turn"]))
            )
    for evts in hurt_events.values():
        evts.sort()
    print("done")

    # ------------------------------------------------------------------
    # Pass 4: precompute turn-level social features.
    # ------------------------------------------------------------------
    print("Pass 4: turn-level social features …", end=" ", flush=True)

    # action counts per turn
    turn_action_counts: dict[tuple[str, int, int], dict[str, int]] = {}
    for key, rows in by_turn.items():
        counts: dict[str, int] = {"HELP": 0, "HURT": 0, "HOARD": 0}
        for r in rows:
            counts[r["action"]] = counts.get(r["action"], 0) + 1
        turn_action_counts[key] = counts

    # pile-on: how many times was each player HURTed this turn
    pile_on_counts: dict[tuple[str, int, int], dict[int, int]] = {}
    for key, rows in by_turn.items():
        target_hits: dict[int, int] = defaultdict(int)
        for r in rows:
            if r["action"] == "HURT" and r["target_player_id"]:
                target_hits[int(r["target_player_id"])] += 1
        pile_on_counts[key] = dict(target_hits)

    # mutual help: pids that gave AND received help in the same turn
    mutual_help_pids: dict[tuple[str, int, int], set[int]] = {}
    for key, rows in by_turn.items():
        helped: dict[int, int] = {}  # giver -> receiver
        for r in rows:
            if r["action"] == "HELP" and r["target_player_id"]:
                helped[int(r["player_id"])] = int(r["target_player_id"])
        mutual: set[int] = set()
        for giver, receiver in helped.items():
            if helped.get(receiver) == giver:
                mutual.add(giver)
                mutual.add(receiver)
        mutual_help_pids[key] = mutual

    print("done")

    # ------------------------------------------------------------------
    # Pass 5: precompute cumulative match-action counts up to each
    # (mid, rnd, turn) boundary (for match_help_rate / match_hurt_rate).
    # We need counts BEFORE the current turn (not including it).
    # ------------------------------------------------------------------
    print("Pass 5: cumulative match action rates …", end=" ", flush=True)

    # Sorted list of all (mid, rnd, turn) keys
    all_turn_keys = sorted(by_turn.keys())

    # For each (mid, rnd, turn), cumulative counts of all match actions
    # seen in turns strictly BEFORE this turn (across all players).
    # Build by iterating in order and keeping a running total per match.
    match_running: dict[str, dict[str, int]] = defaultdict(
        lambda: {"HELP": 0, "HURT": 0, "HOARD": 0, "total": 0}
    )
    # cumulative counts BEFORE this turn key
    before_counts: dict[tuple[str, int, int], dict[str, int]] = {}

    prev_key: tuple[str, int, int] | None = None
    for key in all_turn_keys:
        mid = key[0]
        if prev_key is not None and prev_key[0] == mid:
            # snapshot BEFORE adding this turn's actions
            before_counts[key] = dict(match_running[mid])
        else:
            # first turn for this match (or new match)
            before_counts[key] = {"HELP": 0, "HURT": 0, "HOARD": 0, "total": 0}
        # now add this turn's actions to the running total
        for r in by_turn[key]:
            act = r["action"]
            match_running[mid][act] = match_running[mid].get(act, 0) + 1
            match_running[mid]["total"] += 1
        prev_key = key

    print("done")

    # ------------------------------------------------------------------
    # Pass 6: precompute round_wins_leader per (mid, rnd) and per-player
    # round_wins entering each round (from round_wins_snap).
    # ------------------------------------------------------------------
    print("Pass 6: round-wins leadership history …", end=" ", flush=True)

    # round_wins_entering[(mid, rnd, pid)] = round_wins_before for this player
    # Reconstruct from the CSV: round_wins_before is constant within a round.
    round_wins_entering: dict[tuple[str, int, int], float] = {}
    for row in all_rows:
        mid, rnd, pid = row["match_id"], int(row["round"]), int(row["player_id"])
        if (mid, rnd, pid) not in round_wins_entering:
            round_wins_entering[(mid, rnd, pid)] = float(row["round_wins_before"])

    # players_by_match[mid] -> set of pids
    players_by_match: dict[str, set[int]] = defaultdict(set)
    for row in all_rows:
        players_by_match[row["match_id"]].add(int(row["player_id"]))

    # leader_at_round[(mid, rnd)] = pid with most round_wins_before (None if tied)
    def _leader_at_round(mid: str, rnd: int) -> int | None:
        pids = list(players_by_match[mid])
        wins = [(pid, round_wins_entering.get((mid, rnd, pid), 0.0)) for pid in pids]
        best = max(w for _, w in wins)
        leaders = [p for p, w in wins if w == best]
        return leaders[0] if len(leaders) == 1 else None

    print("done")

    # ------------------------------------------------------------------
    # Pass 7: write enriched output row by row.
    # ------------------------------------------------------------------
    print("Pass 7: writing enriched CSV …", end=" ", flush=True)

    original_fieldnames = list(all_rows[0].keys()) if all_rows else []
    out_fieldnames = original_fieldnames + NEW_COLUMNS

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows_written = 0
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fieldnames)
        writer.writeheader()

        for row in all_rows:
            mid = row["match_id"]
            rnd = int(row["round"])
            turn = int(row["turn"])
            pid = int(row["player_id"])
            total_rounds = int(row["total_rounds"])
            turn_key = (mid, rnd, turn)

            # --- behavioral history ---
            prior = [
                r for r in by_player[(mid, pid)]
                if (int(r["round"]), int(r["turn"])) < (rnd, turn)
            ]
            help_count = sum(1 for r in prior if r["action"] == "HELP")
            hurt_count = sum(1 for r in prior if r["action"] == "HURT")
            hoard_count = sum(1 for r in prior if r["action"] == "HOARD")

            # times targeted: HURT events on this player before this turn
            evts = hurt_events.get((mid, pid), [])
            times_targeted = sum(1 for (er, et) in evts if (er, et) < (rnd, turn))

            # --- turn-level social ---
            act_counts = turn_action_counts.get(turn_key, {})
            tbl_help = act_counts.get("HELP", 0)
            tbl_hurt = act_counts.get("HURT", 0)
            tbl_hoard = act_counts.get("HOARD", 0)

            pile = pile_on_counts.get(turn_key, {})
            was_piled = 1 if pile.get(pid, 0) >= 2 else 0
            pile_max = max(pile.values()) if pile else 0

            mutual = 1 if pid in mutual_help_pids.get(turn_key, set()) else 0

            # --- momentum ---
            consec = 0
            for r in range(rnd - 1, 0, -1):
                if round_won.get((mid, r, pid), False):
                    consec += 1
                else:
                    break

            if len(prior) > 0:
                last_pts = int(prior[-1]["points_delta"])
            else:
                last_pts = 0

            # --- match-level action rates (before this turn) ---
            bc = before_counts.get(turn_key, {})
            total_prior = bc.get("total", 0)
            match_help_rate = round(bc.get("HELP", 0) / total_prior, 4) if total_prior else 0.0
            match_hurt_rate = round(bc.get("HURT", 0) / total_prior, 4) if total_prior else 0.0

            # --- end-game pressure ---
            rw_before = float(row["round_wins_before"])
            clinch_threshold = total_rounds / 2
            self_clinch = 1 if rw_before + 1 > clinch_threshold else 0

            rw_leader = float(row["round_wins_leader"])
            rw_rank = int(row["round_wins_rank"])
            leader_clinch = (
                1 if rw_rank > 1 and rw_leader + 1 > clinch_threshold else 0
            )

            # --- leader stability ---
            current_leader = _leader_at_round(mid, rnd)
            same_leader = 0
            if current_leader is not None:
                for r in range(rnd - 1, 0, -1):
                    if _leader_at_round(mid, r) == current_leader:
                        same_leader += 1
                    else:
                        break

            writer.writerow({
                **row,
                "help_count": help_count,
                "hurt_count": hurt_count,
                "hoard_count": hoard_count,
                "times_targeted": times_targeted,
                "table_help_count": tbl_help,
                "table_hurt_count": tbl_hurt,
                "table_hoard_count": tbl_hoard,
                "was_piled_on": was_piled,
                "pile_on_max": pile_max,
                "got_mutual_help": mutual,
                "consecutive_round_wins": consec,
                "last_points_delta": last_pts,
                "match_help_rate": match_help_rate,
                "match_hurt_rate": match_hurt_rate,
                "self_can_clinch": self_clinch,
                "leader_can_clinch": leader_clinch,
                "rounds_same_leader": same_leader,
            })
            rows_written += 1

    print(f"{rows_written:,} rows written")
    return rows_written


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute derived features for training")
    ap.add_argument("--csv", default="data/baseline.csv", help="Input CSV path")
    ap.add_argument(
        "--out", default="data/baseline_features.csv", help="Output CSV path"
    )
    args = ap.parse_args()
    n = compute(args.csv, args.out)
    print(f"Done. {n:,} rows → {args.out}")


if __name__ == "__main__":
    main()
