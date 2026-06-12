"""Unit tests for app/engine/win_probability.py.

Tests cover:
- Feature vector lengths and spot values (no model file needed)
- Behavioral history counts (help/hurt/hoard, times_targeted)
- Round-winner derivation and consecutive-wins streak
- Table social features (pile-on, mutual help)
- Public API returns {} without model files, and correct shape when mocked
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.win_probability import (
    _Ctx,
    _score_before,
    score_match_win,
    score_round_win,
)

MATCH_FEATURE_COUNT = 29
ROUND_FEATURE_COUNT = 20


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
    import numpy as np

    m = MagicMock()
    m.predict_proba.return_value = np.array([[1 - prob, prob]])
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
