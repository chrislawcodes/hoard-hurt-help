#!/usr/bin/env python3
"""Train a win-probability model on the baseline tournament dataset.

Input:  data/baseline.csv           (one row per player-turn, 21 columns)
Output: data/win_prob_model.pkl     (serialized scikit-learn Pipeline)

The model predicts — for a given player at the START of a turn — the
probability that player wins the match.  Features are pure game-state
(no strategy identity), so the model is deployable for human players too.

Features
--------
round_frac, turn_frac     positional (0→1 through the game)
score_before              focal player's round score entering this turn
round_wins_before         focal player's cumulative round wins
score_rank                rank among all players by score (1 = leader)
score_gap_to_leader       leader score − focal score
score_mean, score_std     distribution of scores across all players
round_wins_rank           rank by round wins (1 = leader)
round_wins_leader         max round wins in the match so far
round_wins_mean           mean round wins across all players
n_players                 number of players in the match

Split: 80/20 by match_id (not by row) to prevent leakage.

Run:
    python scripts/train_win_prob.py
    python scripts/train_win_prob.py --csv data/baseline.csv
    python scripts/train_win_prob.py --csv data/baseline.csv --out data/win_prob_model.pkl
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from winprob_training import run_training_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Feature definition (must stay in sync with app/engine/win_probability.py
# when that module is added).
# ---------------------------------------------------------------------------

FEATURE_NAMES: list[str] = [
    # positional
    "round_frac",
    "turn_frac",
    # focal player state
    "score_before",
    "round_wins_before",
    # scoreboard context
    "score_rank",
    "score_gap_to_leader",
    "score_mean",
    "score_std",
    "round_wins_rank",
    "round_wins_leader",
    "round_wins_mean",
    "n_players",
    # behavioral history
    "help_count",
    "hurt_count",
    "hoard_count",
    "times_targeted",
    # turn-level social
    "table_help_count",
    "table_hurt_count",
    "table_hoard_count",
    "was_piled_on",
    "pile_on_max",
    "got_mutual_help",
    # momentum
    "consecutive_round_wins",
    "last_points_delta",
    # table dynamics
    "match_help_rate",
    "match_hurt_rate",
    # end-game pressure
    "self_can_clinch",
    "leader_can_clinch",
    # leader stability
    "rounds_same_leader",
]

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _row_to_features(row: dict[str, str]) -> list[float]:
    total_rounds = max(int(row["total_rounds"]) - 1, 1)
    turns_per_round = max(int(row["turns_per_round"]) - 1, 1)
    return [
        (int(row["round"]) - 1) / total_rounds,
        (int(row["turn"]) - 1) / turns_per_round,
        float(row["score_before"]),
        float(row["round_wins_before"]),
        float(row["score_rank"]),
        float(row["score_gap_to_leader"]),
        float(row["score_mean"]),
        float(row["score_std"]),
        float(row["round_wins_rank"]),
        float(row["round_wins_leader"]),
        float(row["round_wins_mean"]),
        float(row["n_players"]),
        float(row["help_count"]),
        float(row["hurt_count"]),
        float(row["hoard_count"]),
        float(row["times_targeted"]),
        float(row["table_help_count"]),
        float(row["table_hurt_count"]),
        float(row["table_hoard_count"]),
        float(row["was_piled_on"]),
        float(row["pile_on_max"]),
        float(row["got_mutual_help"]),
        float(row["consecutive_round_wins"]),
        float(row["last_points_delta"]),
        float(row["match_help_rate"]),
        float(row["match_hurt_rate"]),
        float(row["self_can_clinch"]),
        float(row["leader_can_clinch"]),
        float(row["rounds_same_leader"]),
    ]


def load_dataset(
    csv_path: str,
) -> tuple[list[str], list[list[float]], list[int]]:
    """Return (match_ids, X, y)."""
    match_ids: list[str] = []
    X: list[list[float]] = []
    y: list[int] = []

    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            match_ids.append(row["match_id"])
            X.append(_row_to_features(row))
            y.append(int(row["match_won"]))

    return match_ids, X, y


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    run_training_cli(
        description="Train win-probability model",
        default_csv="data/baseline_features.csv",
        default_out="data/win_prob_model.pkl",
        feature_names=FEATURE_NAMES,
        load_dataset=load_dataset,
        loading_message="Loading {csv} …",
        title="Win-Probability Model",
        pos_label="wins",
    )


if __name__ == "__main__":
    main()
