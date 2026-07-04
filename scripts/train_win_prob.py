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

# The vocabulary import sits with the other top-of-file imports: `app` is an
# installed package (run these scripts via the project venv), so it needs no
# path bootstrap. Only the sibling `winprob_training` module does.
from app.engine.win_prob_features import MATCH_FEATURE_NAMES

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from winprob_training import feature_value_from_row, run_training_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Feature definition — single-sourced from app/engine/win_prob_features.py,
# the same vocabulary app/engine/win_probability.py builds vectors from.
# ---------------------------------------------------------------------------

FEATURE_NAMES: list[str] = list(MATCH_FEATURE_NAMES)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _row_to_features(row: dict[str, str]) -> list[float]:
    return [feature_value_from_row(name, row) for name in MATCH_FEATURE_NAMES]


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
