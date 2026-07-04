"""Unit tests for app/engine/win_probability.py.

Tests cover:
- Golden feature vectors pinning the exact values (and order) the trained
  pickles in data/ expect — captured from the pre-refactor positional builders
- Feature vector lengths and spot values (no model file needed)
- Feature-order alignment: engine named dicts, the shared vocabulary in
  app/engine/win_prob_features.py, and the trainers' FEATURE_NAMES all agree
- Behavioral history counts (help/hurt/hoard, times_targeted)
- Round-winner derivation and consecutive-wins streak
- Table social features (pile-on, mutual help)
- Public API returns {} without model files, and correct shape when mocked
"""

from __future__ import annotations

import importlib.util
import pickle
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.win_prob_features import (
    DERIVED_FEATURE_COLUMNS,
    MATCH_FEATURE_NAMES,
    ROUND_FEATURE_NAMES,
    feature_vector,
)
from app.engine.win_probability import (
    _Ctx,
    _score_before,
    score_match_win,
    score_round_win,
)

MATCH_FEATURE_COUNT = len(MATCH_FEATURE_NAMES)
ROUND_FEATURE_COUNT = len(ROUND_FEATURE_NAMES)

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def player(agent_id: str, round_score: int = 0, round_wins: float = 0.0) -> PlayerRecord:
    return PlayerRecord(
        agent_id=agent_id,
        round_score=round_score,
        total_score=round_score,
        round_wins=round_wins,
    )


def act(
    rnd: int,
    turn: int,
    actor: str,
    action: Action,
    target: str | None = None,
    score: int = 0,
    delta: int = 0,
) -> ActionRecord:
    return ActionRecord(
        round=rnd,
        turn=turn,
        actor_id=actor,
        action=action,
        target_id=target,
        message="",
        points_delta=delta,
        round_score_after=score,
        was_defaulted=False,
    )


PLAYERS = [
    player("A", round_score=4, round_wins=1.0),
    player("B", round_score=2, round_wins=0.0),
    player("C", round_score=2, round_wins=0.0),
]

# 2 rounds × 3 turns; round 1 complete (A won), currently in round 2 turn 3
ACTIONS = [
    # Round 1
    act(1, 1, "A", "HELP", "B", score=0, delta=0),
    act(1, 1, "B", "HOARD", score=2, delta=2),
    act(1, 1, "C", "HURT", "B", score=0, delta=0),
    act(1, 2, "A", "HURT", "C", score=0, delta=0),
    act(1, 2, "B", "HOARD", score=4, delta=2),
    act(1, 2, "C", "HOARD", score=2, delta=2),
    act(1, 3, "A", "HOARD", score=2, delta=2),
    act(1, 3, "B", "HOARD", score=6, delta=2),  # B wins round 1 (score 6)
    act(1, 3, "C", "HOARD", score=4, delta=2),
    # Round 2
    act(2, 1, "A", "HELP", "C", score=0, delta=0),
    act(2, 1, "B", "HURT", "A", score=0, delta=0),
    act(2, 1, "C", "HOARD", score=2, delta=2),
    act(2, 2, "A", "HURT", "B", score=0, delta=0),
    act(2, 2, "B", "HURT", "A", score=0, delta=0),
    act(2, 2, "C", "HURT", "A", score=2, delta=0),  # A piled-on by B+C
    act(2, 3, "A", "HOARD", score=2, delta=2),
    act(2, 3, "B", "HURT", "C", score=0, delta=0),
    act(2, 3, "C", "HELP", "A", score=4, delta=4),
]


# ---------------------------------------------------------------------------
# _score_before
# ---------------------------------------------------------------------------


def test_score_before_first_turn() -> None:
    assert _score_before("A", ACTIONS, current_round=1, current_turn=1) == 0.0


def test_score_before_mid_round() -> None:
    # After round 2 turn 2, A's round_score_after was 0 → score_before for turn 3 = 0
    assert _score_before("A", ACTIONS, current_round=2, current_turn=3) == 0.0


def test_score_before_uses_previous_turn() -> None:
    # C's round_score_after at round 2 turn 2 = 2 → score_before for turn 3 = 2
    assert _score_before("C", ACTIONS, current_round=2, current_turn=3) == 2.0


# ---------------------------------------------------------------------------
# Feature vector lengths
# ---------------------------------------------------------------------------


def test_match_feature_length() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    assert len(ctx.match_features("A")) == MATCH_FEATURE_COUNT


def test_round_feature_length() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    assert len(ctx.round_features("A")) == ROUND_FEATURE_COUNT


def test_all_players_same_feature_length() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    for p in PLAYERS:
        assert len(ctx.match_features(p.agent_id)) == MATCH_FEATURE_COUNT
        assert len(ctx.round_features(p.agent_id)) == ROUND_FEATURE_COUNT


# ---------------------------------------------------------------------------
# Golden vectors — exact values captured from the pre-refactor positional
# builders on the fixtures above (round 2, turn 3, 2 rounds x 3 turns).
# These pin both the values AND the order the trained pickles in data/ expect.
# Any diff here means inference inputs changed: retrain or revert.
# ---------------------------------------------------------------------------

GOLDEN_MATCH_FEATURES: dict[str, list[float]] = {
    "A": [1.0, 1.0, 0.0, 1.0, 4.0, 4.0, 2.6666666666666665, 0.9428090415820634,
          1.0, 1.0, 0.3333333333333333, 3.0, 2.0, 2.0, 1.0, 3.0, 1.0, 1.0, 1.0,
          0.0, 1.0, 0.0, 0.0, 0.0, 0.13333333333333333, 0.4, 1.0, 0.0, 0.0],
    "B": [1.0, 1.0, 0.0, 0.0, 4.0, 4.0, 2.6666666666666665, 0.9428090415820634,
          2.0, 1.0, 0.3333333333333333, 3.0, 0.0, 2.0, 3.0, 2.0, 1.0, 1.0, 1.0,
          0.0, 1.0, 0.0, 1.0, 0.0, 0.13333333333333333, 0.4, 0.0, 1.0, 0.0],
    "C": [1.0, 1.0, 2.0, 0.0, 2.0, 2.0, 2.6666666666666665, 0.9428090415820634,
          2.0, 1.0, 0.3333333333333333, 3.0, 0.0, 2.0, 3.0, 1.0, 1.0, 1.0, 1.0,
          0.0, 1.0, 0.0, 0.0, 0.0, 0.13333333333333333, 0.4, 0.0, 1.0, 0.0],
}

GOLDEN_ROUND_FEATURES: dict[str, list[float]] = {
    "A": [1.0, 0.0, 4.0, 4.0, 2.6666666666666665, 0.9428090415820634, 3.0,
          2.0, 2.0, 1.0, 3.0, 1.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0,
          0.13333333333333333, 0.4],
    "B": [1.0, 0.0, 4.0, 4.0, 2.6666666666666665, 0.9428090415820634, 3.0,
          0.0, 2.0, 3.0, 2.0, 1.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0,
          0.13333333333333333, 0.4],
    "C": [1.0, 2.0, 2.0, 2.0, 2.6666666666666665, 0.9428090415820634, 3.0,
          0.0, 2.0, 3.0, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0,
          0.13333333333333333, 0.4],
}


def test_golden_match_features_unchanged() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    for agent_id, expected in GOLDEN_MATCH_FEATURES.items():
        assert ctx.match_features(agent_id) == expected


def test_golden_round_features_unchanged() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    for agent_id, expected in GOLDEN_ROUND_FEATURES.items():
        assert ctx.round_features(agent_id) == expected


# ---------------------------------------------------------------------------
# Feature-order alignment — engine, shared vocabulary, and trainers
# ---------------------------------------------------------------------------


def test_match_named_features_follow_shared_order() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    named = ctx.match_features_named("A")
    assert tuple(named.keys()) == MATCH_FEATURE_NAMES
    assert ctx.match_features("A") == [named[n] for n in MATCH_FEATURE_NAMES]


def test_round_named_features_follow_shared_order() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    named = ctx.round_features_named("A")
    assert tuple(named.keys()) == ROUND_FEATURE_NAMES
    assert ctx.round_features("A") == [named[n] for n in ROUND_FEATURE_NAMES]


def test_feature_vector_rejects_vocabulary_mismatch() -> None:
    with pytest.raises(ValueError, match="missing=\\['b'\\], extra=\\['c'\\]"):
        feature_vector({"a": 1.0, "c": 2.0}, ("a", "b"))


def _load_script(name: str) -> ModuleType:
    """Import a scripts/ module by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_trainers_and_feature_pipeline_share_engine_vocabulary() -> None:
    # Import smoke: the three pipeline scripts load cleanly outside a package
    # context and expose the shared vocabulary (their lists are built from the
    # engine tuples, so equality here mostly guards against a re-pasted literal).
    train_match = _load_script("train_win_prob")
    train_round = _load_script("train_round_win_prob")
    compute_features = _load_script("compute_features")

    assert train_match.FEATURE_NAMES == list(MATCH_FEATURE_NAMES)
    assert train_round.FEATURE_NAMES == list(ROUND_FEATURE_NAMES)
    assert compute_features.NEW_COLUMNS == list(DERIVED_FEATURE_COLUMNS)


def test_shipped_models_were_trained_on_shared_vocabulary() -> None:
    """The real drift guard: the pickled models' baked-in feature order must
    equal the live vocabulary, or inference feeds them mis-aligned vectors."""
    for pkl_name, names in (
        ("win_prob_model.pkl", MATCH_FEATURE_NAMES),
        ("round_win_prob_model.pkl", ROUND_FEATURE_NAMES),
    ):
        with open(_SCRIPTS_DIR.parent / "data" / pkl_name, "rb") as fh:
            payload = pickle.load(fh)
        assert payload["feature_names"] == list(names), pkl_name


def test_trainer_row_reader_matches_engine_order() -> None:
    """_row_to_features reads CSV columns in the exact shared-name order."""
    train_match = _load_script("train_win_prob")
    row = {name: str(float(i + 1)) for i, name in enumerate(MATCH_FEATURE_NAMES)}
    # round_frac / turn_frac are derived from raw positional columns.
    row.update({
        "round": "2", "total_rounds": "3",
        "turn": "3", "turns_per_round": "5",
    })
    feats = train_match._row_to_features(row)
    assert len(feats) == MATCH_FEATURE_COUNT
    assert feats[0] == pytest.approx((2 - 1) / (3 - 1))  # round_frac
    assert feats[1] == pytest.approx((3 - 1) / (5 - 1))  # turn_frac
    for i, name in enumerate(MATCH_FEATURE_NAMES):
        if name in ("round_frac", "turn_frac"):
            continue
        assert feats[i] == float(row[name])


# ---------------------------------------------------------------------------
# Behavioral history counts
# ---------------------------------------------------------------------------


def test_help_count() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    # A: HELP at (1,1) and HELP at (2,1) — both before (2,3)
    feats = ctx.match_features("A")
    help_idx = 12
    assert feats[help_idx] == 2.0


def test_hurt_count() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    # A: HURT at (1,2) and HURT at (2,2) before (2,3)
    feats = ctx.match_features("A")
    hurt_idx = 13
    assert feats[hurt_idx] == 2.0


def test_times_targeted() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    # A was HURTed by B at (2,1), B at (2,2), C at (2,2) — all before (2,3)
    feats = ctx.match_features("A")
    targeted_idx = 15
    assert feats[targeted_idx] == 3.0


# ---------------------------------------------------------------------------
# Round-winner derivation and consecutive wins
# ---------------------------------------------------------------------------


def test_round_winner_detected() -> None:
    # Round 1: B has score 6 (highest) → B won round 1
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    assert ctx._round_winners == {1: {"B"}}


def test_consecutive_round_wins_none() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    # A won no prior rounds
    assert ctx._consec_rw("A") == 0


def test_consecutive_round_wins_one() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    assert ctx._consec_rw("B") == 1


# ---------------------------------------------------------------------------
# Table social features (pile-on, mutual help)
# ---------------------------------------------------------------------------


def test_was_piled_on() -> None:
    # At (2,2), A was HURTed by both B and C
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=2, total_rounds=2, turns_per_round=3)
    feats = ctx.match_features("A")
    piled_idx = 19
    assert feats[piled_idx] == 1.0


def test_pile_max() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=2, total_rounds=2, turns_per_round=3)
    feats = ctx.match_features("A")
    pile_max_idx = 20
    assert feats[pile_max_idx] == 2.0


def test_mutual_help_detected() -> None:
    # At (2,3): C HELPs A, but A HOARDs — no mutual help
    # Build a turn where A helps C and C helps A
    mutual_actions = [
        act(1, 1, "A", "HELP", "C", score=0),
        act(1, 1, "C", "HELP", "A", score=0),
        act(1, 1, "B", "HOARD", score=2),
    ]
    players = [player("A"), player("B"), player("C")]
    ctx = _Ctx(players, mutual_actions, current_round=1, current_turn=1, total_rounds=3, turns_per_round=3)
    feats = ctx.match_features("A")
    mutual_idx = 21
    assert feats[mutual_idx] == 1.0


# ---------------------------------------------------------------------------
# Position features
# ---------------------------------------------------------------------------


def test_turn_frac_last_turn() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    # turn_frac = (3-1) / (3-1) = 1.0
    feats = ctx.match_features("A")
    assert feats[1] == pytest.approx(1.0)


def test_round_frac_last_round() -> None:
    ctx = _Ctx(PLAYERS, ACTIONS, current_round=2, current_turn=3, total_rounds=2, turns_per_round=3)
    # round_frac = (2-1) / (2-1) = 1.0
    feats = ctx.match_features("A")
    assert feats[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Public API — no model file → returns {}
# ---------------------------------------------------------------------------


def test_score_match_win_no_model(tmp_path: Any) -> None:
    with patch("app.engine.win_probability._MATCH_MODEL_PATH", tmp_path / "missing.pkl"):
        with patch("app.engine.win_probability._model_cache", {}):
            result = score_match_win(PLAYERS, ACTIONS, 2, 3, 2, 3)
    assert result == {}


def test_score_round_win_no_model(tmp_path: Any) -> None:
    with patch("app.engine.win_probability._ROUND_MODEL_PATH", tmp_path / "missing.pkl"):
        with patch("app.engine.win_probability._model_cache", {}):
            result = score_round_win(PLAYERS, ACTIONS, 2, 3, 3)
    assert result == {}


# ---------------------------------------------------------------------------
# Public API — mocked model
# ---------------------------------------------------------------------------


def _make_mock_model(prob: float = 0.5) -> MagicMock:
    m = MagicMock()
    m.predict_proba.return_value = [[1 - prob, prob]]
    return m


def test_score_match_win_returns_all_agents() -> None:
    mock = _make_mock_model(0.4)
    with patch("app.engine.win_probability._model_cache", {"match": mock}):
        result = score_match_win(PLAYERS, ACTIONS, 2, 3, 2, 3)
    assert set(result.keys()) == {"A", "B", "C"}


def test_score_match_win_probability_value() -> None:
    mock = _make_mock_model(0.4)
    with patch("app.engine.win_probability._model_cache", {"match": mock}):
        result = score_match_win(PLAYERS, ACTIONS, 2, 3, 2, 3)
    for v in result.values():
        assert v == pytest.approx(0.4)


def test_score_round_win_returns_all_agents() -> None:
    mock = _make_mock_model(0.3)
    with patch("app.engine.win_probability._model_cache", {"round": mock}):
        result = score_round_win(PLAYERS, ACTIONS, 2, 3, 3)
    assert set(result.keys()) == {"A", "B", "C"}


def test_score_match_win_empty_players() -> None:
    mock = _make_mock_model()
    with patch("app.engine.win_probability._model_cache", {"match": mock}):
        assert score_match_win([], ACTIONS, 2, 3, 2, 3) == {}
