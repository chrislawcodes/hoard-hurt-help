"""Single source of truth for the win-probability feature vocabularies.

The win-probability models are positional: the pickled scikit-learn pipelines
in data/win_prob_model.pkl and data/round_win_prob_model.pkl were trained on
feature vectors in EXACTLY the order declared here. Three code sites must
agree on that order:

  1. scripts/compute_features.py — writes the derived CSV columns
     (DERIVED_FEATURE_COLUMNS) into data/baseline_features.csv.
  2. scripts/train_win_prob.py / scripts/train_round_win_prob.py — read those
     columns into training matrices (MATCH_FEATURE_NAMES / ROUND_FEATURE_NAMES).
  3. app/engine/win_probability.py — rebuilds the same vectors live at
     inference time.

This module is the one place the names and their order live. Do NOT reorder,
rename, insert, or remove entries without retraining both models — a silent
reorder corrupts every prediction while all shapes still line up.
tests/test_win_probability.py pins the order with golden vectors and
name-order assertions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# The 17 derived columns scripts/compute_features.py appends to the raw
# baseline export. Both trainers read these back by name.
DERIVED_FEATURE_COLUMNS: tuple[str, ...] = (
    "help_count",
    "hurt_count",
    "hoard_count",
    "times_targeted",
    "table_help_count",
    "table_hurt_count",
    "table_hoard_count",
    "was_piled_on",
    "pile_on_max",
    "got_mutual_help",
    "consecutive_round_wins",
    "last_points_delta",
    "match_help_rate",
    "match_hurt_rate",
    "self_can_clinch",
    "leader_can_clinch",
    "rounds_same_leader",
)

# Input order for data/win_prob_model.pkl (match-win model).
MATCH_FEATURE_NAMES: tuple[str, ...] = (
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
)

# Input order for data/round_win_prob_model.pkl (round-win model).
ROUND_FEATURE_NAMES: tuple[str, ...] = (
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
)


def feature_vector(named: Mapping[str, float], names: Sequence[str]) -> list[float]:
    """Materialize the ordered feature vector from a {name: value} mapping.

    Fails loud on any vocabulary mismatch: a missing name raises, and extra
    names raise, so a builder that drifts from the shared vocabulary can never
    silently feed a misaligned vector to a model.
    """
    if set(named) != set(names):
        missing = sorted(set(names) - set(named))
        extra = sorted(set(named) - set(names))
        raise ValueError(
            f"feature mapping does not match vocabulary: missing={missing}, extra={extra}"
        )
    return [float(named[name]) for name in names]
