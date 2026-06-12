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

import argparse
import csv
import pickle
import random
from collections import defaultdict
from pathlib import Path


FEATURE_NAMES: list[str] = [
    # within-round state
    "turn_frac",
    "score_before",
    "score_rank",
    "score_gap_to_leader",
    "score_mean",
    "score_std",
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
    "last_points_delta",
    # table dynamics
    "match_help_rate",
    "match_hurt_rate",
]

TRAIN_FRAC = 0.8
RANDOM_STATE = 42


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
            turn = int(row["turn"])
            pid = int(row["player_id"])
            turns_per_round = max(int(row["turns_per_round"]) - 1, 1)

            label = round_won.get((mid, rnd, pid))
            if label is None:
                continue

            features = [
                (turn - 1) / turns_per_round,
                float(row["score_before"]),
                float(row["score_rank"]),
                float(row["score_gap_to_leader"]),
                float(row["score_mean"]),
                float(row["score_std"]),
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
                float(row["last_points_delta"]),
                float(row["match_help_rate"]),
                float(row["match_hurt_rate"]),
            ]
            match_ids.append(mid)
            X.append(features)
            y.append(label)

    return match_ids, X, y


def split_by_match(
    match_ids: list[str],
    X: list[list[float]],
    y: list[int],
    *,
    train_frac: float,
    seed: int,
) -> tuple[list[list[float]], list[int], list[list[float]], list[int]]:
    all_matches = list(dict.fromkeys(match_ids))
    rng = random.Random(seed)
    rng.shuffle(all_matches)
    split = int(len(all_matches) * train_frac)
    train_set = set(all_matches[:split])

    X_train, y_train, X_test, y_test = [], [], [], []
    for mid, feats, label in zip(match_ids, X, y):
        if mid in train_set:
            X_train.append(feats)
            y_train.append(label)
        else:
            X_test.append(feats)
            y_test.append(label)

    return X_train, y_train, X_test, y_test


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(X_train: list[list[float]], y_train: list[int]):  # type: ignore[return]
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(
        max_iter=400,
        learning_rate=0.05,
        max_depth=4,
        min_samples_leaf=30,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)
    return model


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(model, X_test: list[list[float]], y_test: list[int]) -> None:  # type: ignore[no-untyped-def]
    import numpy as np
    from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
    from sklearn.inspection import permutation_importance

    probs = model.predict_proba(X_test)[:, 1]
    n = len(y_test)
    pos_rate = sum(y_test) / n

    auc = roc_auc_score(y_test, probs)
    ll = log_loss(y_test, probs)
    brier = brier_score_loss(y_test, probs)

    print(f"\n{'='*56}")
    print("  Round-Win-Probability Model — Evaluation")
    print(f"{'='*56}")
    print(f"  Test rows:       {n:,}  ({pos_rate:.1%} round wins)")
    print(f"  ROC-AUC:         {auc:.4f}")
    print(f"  Log loss:        {ll:.4f}")
    print(f"  Brier score:     {brier:.4f}")

    print(f"\n  {'Predicted':>12}  {'Actual win%':>12}  {'N':>7}")
    print(f"  {'-'*12}  {'-'*12}  {'-'*7}")
    buckets: dict[int, list[float]] = {}
    bucket_y: dict[int, list[int]] = {}
    for p, label in zip(probs, y_test):
        b = min(int(p * 10), 9)
        buckets.setdefault(b, []).append(p)
        bucket_y.setdefault(b, []).append(label)
    for b in range(10):
        if b not in buckets:
            continue
        mean_pred = sum(buckets[b]) / len(buckets[b])
        actual = sum(bucket_y[b]) / len(bucket_y[b])
        print(f"  {mean_pred:>12.3f}  {actual:>12.3f}  {len(bucket_y[b]):>7,}")

    perm = permutation_importance(
        model,
        np.array(X_test),
        y_test,
        n_repeats=5,
        random_state=RANDOM_STATE,
        scoring="roc_auc",
    )
    print("  Feature importances (permutation, Δ ROC-AUC):")
    importances = list(zip(FEATURE_NAMES, perm.importances_mean))
    importances.sort(key=lambda x: -x[1])
    for name, imp in importances:
        bar = "█" * max(0, int(imp * 200))
        print(f"  {name:<22} {imp:+.4f}  {bar}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Train round-win-probability model")
    ap.add_argument("--csv", default="data/baseline_features.csv", help="Input CSV path")
    ap.add_argument(
        "--out",
        default="data/round_win_prob_model.pkl",
        help="Output model path",
    )
    ap.add_argument(
        "--train-frac",
        type=float,
        default=TRAIN_FRAC,
        help="Fraction of matches used for training (default 0.8)",
    )
    args = ap.parse_args()

    print(f"Loading {args.csv} and deriving round_won …", end=" ", flush=True)
    match_ids, X, y = load_dataset(args.csv)
    unique_matches = len(set(match_ids))
    pos_rate = sum(y) / len(y)
    print(f"{len(X):,} rows, {unique_matches} matches, {pos_rate:.1%} round wins")

    X_train, y_train, X_test, y_test = split_by_match(
        match_ids, X, y, train_frac=args.train_frac, seed=RANDOM_STATE
    )
    n_train = int(unique_matches * args.train_frac)
    n_test = unique_matches - n_train
    print(
        f"Split: {n_train} train matches ({len(X_train):,} rows) / "
        f"{n_test} test matches ({len(X_test):,} rows)"
    )

    print("Training HistGradientBoostingClassifier …", end=" ", flush=True)
    model = train(X_train, y_train)
    print("done")

    evaluate(model, X_test, y_test)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        pickle.dump({"model": model, "feature_names": FEATURE_NAMES}, fh)
    print(f"Model saved to {args.out}")


if __name__ == "__main__":
    main()
