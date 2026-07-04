#!/usr/bin/env python3
"""Shared train/evaluate/CLI scaffolding for the win-probability trainers.

scripts/train_win_prob.py and scripts/train_round_win_prob.py both train a
HistGradientBoostingClassifier on the baseline dataset and pickle
`{"model": ..., "feature_names": ...}` for app/engine/win_probability.py to
load. Everything except the dataset loader + feature derivation was
structurally identical between the two copies — this module holds that
common part once: the match-level train/test split, the classifier config,
the evaluation report (ROC-AUC / log-loss / Brier / calibration buckets /
permutation importance), and the CLI + pickle scaffolding.

The two scripts differed in a few small, easy-to-miss ways that this module
parameterizes rather than silently picking one:
  - argparse `description`, and the `--out` default path.
  - the "Loading ..." message printed before `load_dataset` runs (round-win
    prints "... and deriving round_won ...").
  - the post-load summary line: win-prob prints just row/match counts;
    round-win also reports the round-win positive rate (`sum(y) / len(y)`).
  - the evaluation report's title ("Win-Probability Model" vs
    "Round-Win-Probability Model") and the label used for the positive class
    in the "Test rows" line ("wins" vs "round wins").

HARD CONSTRAINT: the pickle payload must stay exactly
`{"model", "feature_names"}` with identical semantics — app/engine/
win_probability.py loads these — and both CLIs must keep accepting the same
`--csv` / `--out` / `--train-frac` arguments as before.
"""

from __future__ import annotations

import argparse
import pickle
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

TRAIN_FRAC = 0.8
RANDOM_STATE = 42

# (match_ids, X, y)
Dataset = tuple[list[str], list[list[float]], list[int]]
LoadDatasetFn = Callable[[str], Dataset]
DescribeLoadedFn = Callable[[list[str], list[list[float]], list[int]], str]


def feature_value_from_row(name: str, row: dict[str, str]) -> float:
    """Resolve one named feature from a baseline-CSV row.

    The one resolver both trainers share: round_frac / turn_frac are derived
    from the raw positional columns (the same `max(total - 1, 1)` denominators
    app/engine/win_probability.py uses live); every other feature is a direct
    float read of the same-named CSV column. round_frac simply never comes up
    for the round model's vocabulary.
    """
    if name == "round_frac":
        total_rounds = max(int(row["total_rounds"]) - 1, 1)
        return (int(row["round"]) - 1) / total_rounds
    if name == "turn_frac":
        turns_per_round = max(int(row["turns_per_round"]) - 1, 1)
        return (int(row["turn"]) - 1) / turns_per_round
    return float(row[name])


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


def split_by_match(
    match_ids: list[str],
    X: list[list[float]],
    y: list[int],
    *,
    train_frac: float,
    seed: int,
) -> tuple[list[list[float]], list[int], list[list[float]], list[int]]:
    all_matches = list(dict.fromkeys(match_ids))  # unique, stable order
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


def train(X_train: list[list[float]], y_train: list[int]) -> Any:
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


def evaluate(
    model: Any,
    X_test: list[list[float]],
    y_test: list[int],
    *,
    feature_names: list[str],
    title: str,
    pos_label: str,
) -> None:
    probs = model.predict_proba(X_test)[:, 1]
    n = len(y_test)

    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    auc = roc_auc_score(y_test, probs)
    ll = log_loss(y_test, probs)
    brier = brier_score_loss(y_test, probs)

    pos_rate = sum(y_test) / n
    print(f"\n{'=' * 56}")
    print(f"  {title} — Evaluation")
    print(f"{'=' * 56}")
    print(f"  Test rows:       {n:,}  ({pos_rate:.1%} {pos_label})")
    print(f"  ROC-AUC:         {auc:.4f}")
    print(f"  Log loss:        {ll:.4f}")
    print(f"  Brier score:     {brier:.4f}")

    # Calibration: bucket by predicted probability decile
    print(f"\n  {'Predicted':>12}  {'Actual win%':>12}  {'N':>7}")
    print(f"  {'-' * 12}  {'-' * 12}  {'-' * 7}")
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
        n_b = len(bucket_y[b])
        print(f"  {mean_pred:>12.3f}  {actual:>12.3f}  {n_b:>7,}")

    # Feature importances via permutation (works for any sklearn estimator)
    import numpy as np
    from sklearn.inspection import permutation_importance

    perm = permutation_importance(
        model,
        np.array(X_test),
        y_test,
        n_repeats=5,
        random_state=RANDOM_STATE,
        scoring="roc_auc",
    )
    print("  Feature importances (permutation, Δ ROC-AUC):")
    importances = list(zip(feature_names, perm.importances_mean))
    importances.sort(key=lambda x: -x[1])
    for name, imp in importances:
        bar = "█" * max(0, int(imp * 200))
        print(f"  {name:<22} {imp:+.4f}  {bar}")

    print()


# ---------------------------------------------------------------------------
# CLI + pickle scaffolding
# ---------------------------------------------------------------------------


def run_training_cli(
    *,
    description: str,
    default_csv: str,
    default_out: str,
    feature_names: list[str],
    load_dataset: LoadDatasetFn,
    loading_message: str,
    title: str,
    pos_label: str,
    describe_loaded: DescribeLoadedFn | None = None,
) -> None:
    """Run the shared argparse -> load -> split -> train -> evaluate -> pickle flow.

    describe_loaded, if given, replaces the default "N rows, M matches" summary
    line printed right after `load_dataset` returns (round-win's trainer uses
    this to also report the round-win positive rate).
    """
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--csv", default=default_csv, help="Input CSV path")
    ap.add_argument("--out", default=default_out, help="Output model path")
    ap.add_argument(
        "--train-frac",
        type=float,
        default=TRAIN_FRAC,
        help="Fraction of matches used for training (default 0.8)",
    )
    args = ap.parse_args()

    print(loading_message.format(csv=args.csv), end=" ", flush=True)
    match_ids, X, y = load_dataset(args.csv)
    unique_matches = len(set(match_ids))
    if describe_loaded is not None:
        print(describe_loaded(match_ids, X, y))
    else:
        print(f"{len(X):,} rows, {unique_matches} matches")

    X_train, y_train, X_test, y_test = split_by_match(
        match_ids, X, y, train_frac=args.train_frac, seed=RANDOM_STATE
    )
    n_train_matches = int(unique_matches * args.train_frac)
    n_test_matches = unique_matches - n_train_matches
    print(
        f"Split: {n_train_matches} train matches ({len(X_train):,} rows) / "
        f"{n_test_matches} test matches ({len(X_test):,} rows)"
    )

    print("Training HistGradientBoostingClassifier …", end=" ", flush=True)
    model = train(X_train, y_train)
    print("done")

    evaluate(
        model,
        X_test,
        y_test,
        feature_names=feature_names,
        title=title,
        pos_label=pos_label,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        pickle.dump({"model": model, "feature_names": feature_names}, fh)
    print(f"Model saved to {args.out}")
