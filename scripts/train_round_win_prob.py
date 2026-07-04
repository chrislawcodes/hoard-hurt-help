#!/usr/bin/env python3
"""Train a round-win-probability model on the baseline tournament dataset.

Input:  data/baseline.csv              (one row per player-turn, 21 columns)
Output: data/round_win_prob_model.pkl  (serialized scikit-learn Pipeline)

The model predicts — for a given player at the START of a turn — the
probability that player wins the current round.

round_won is derived on the fly: for each (match_id, round), the player
with the highest round_score_after on the last turn of that round wins.
Ties are treated as shared wins (each tied player gets round_won=1).

Features
--------
turn_frac             how far through the round (0 = first turn, 1 = last)
score_before          focal player's round score entering this turn
score_rank            rank among all players by score_before (1 = leader)
score_gap_to_leader   leader score − focal score (0 if tied for lead)
score_mean            mean score_before across all players
score_std             std dev of score_before across all players
n_players             number of players in the match

Split: 80/20 by match_id (not by row) to prevent leakage.

Run:
    python scripts/train_round_win_prob.py
    python scripts/train_round_win_prob.py --csv data/baseline.csv
    python scripts/train_round_win_prob.py --csv data/baseline.csv --out data/round_win_prob_model.pkl
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

# The vocabulary import sits with the other top-of-file imports: `app` is an
# installed package (run these scripts via the project venv), so it needs no
# path bootstrap. Only the sibling `winprob_training` module does.
from app.engine.win_prob_features import ROUND_FEATURE_NAMES

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from winprob_training import feature_value_from_row, run_training_cli  # noqa: E402

# Feature definition — single-sourced from app/engine/win_prob_features.py,
# the same vocabulary app/engine/win_probability.py builds vectors from.
FEATURE_NAMES: list[str] = list(ROUND_FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Data loading + round_won derivation
# ---------------------------------------------------------------------------

def load_dataset(
    csv_path: str,
) -> tuple[list[str], list[list[float]], list[int]]:
    """Return (match_ids, X, y) where y=round_won."""

    # First pass: find last turn of each (match_id, round) and collect
    # round_score_after for all players on that last turn.
    last_turn: dict[tuple[str, int], int] = {}
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row["match_id"], int(row["round"]))
            last_turn[key] = max(last_turn.get(key, 0), int(row["turn"]))

    # Second pass: for each (match_id, round), collect final scores.
    final_scores: dict[tuple[str, int], dict[int, int]] = defaultdict(dict)
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            mid, rnd, turn = row["match_id"], int(row["round"]), int(row["turn"])
            if turn == last_turn[(mid, rnd)]:
                final_scores[(mid, rnd)][int(row["player_id"])] = int(
                    row["round_score_after"]
                )

    # Derive round_won per (match_id, round, player_id).
    round_won: dict[tuple[str, int, int], int] = {}
    for (mid, rnd), scores in final_scores.items():
        if not scores:
            continue
        best = max(scores.values())
        for pid, score in scores.items():
            round_won[(mid, rnd, pid)] = 1 if score == best else 0

    # Third pass: build feature matrix.
    match_ids: list[str] = []
    X: list[list[float]] = []
    y: list[int] = []

    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            mid = row["match_id"]
            rnd = int(row["round"])
            pid = int(row["player_id"])

            label = round_won.get((mid, rnd, pid))
            if label is None:
                continue

            features = [
                feature_value_from_row(name, row) for name in ROUND_FEATURE_NAMES
            ]
            match_ids.append(mid)
            X.append(features)
            y.append(label)

    return match_ids, X, y


def _describe_loaded(match_ids: list[str], X: list[list[float]], y: list[int]) -> str:
    unique_matches = len(set(match_ids))
    pos_rate = sum(y) / len(y)
    return f"{len(X):,} rows, {unique_matches} matches, {pos_rate:.1%} round wins"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    run_training_cli(
        description="Train round-win-probability model",
        default_csv="data/baseline_features.csv",
        default_out="data/round_win_prob_model.pkl",
        feature_names=FEATURE_NAMES,
        load_dataset=load_dataset,
        loading_message="Loading {csv} and deriving round_won …",
        title="Round-Win-Probability Model",
        pos_label="round wins",
        describe_loaded=_describe_loaded,
    )


if __name__ == "__main__":
    main()
