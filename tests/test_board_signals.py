"""Unit tests for whole-board signals: temperature, alliances, surging, breaks."""

from __future__ import annotations

from app.engine.game_insights import detect_surging
from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.games.hoard_hurt_help.board_signals import (
    alliance_formed_this_turn,
    compute_board_signals,
    detect_alliances,
    detect_pattern_breaks,
)


def player(agent_id: str, round_score: int = 0) -> PlayerRecord:
    return PlayerRecord(
        agent_id=agent_id, round_score=round_score, total_score=round_score, round_wins=0.0
    )


def act(
    rnd: int,
    turn: int,
    actor: str,
    action: Action,
    target: str | None = None,
    score: int = 0,
) -> ActionRecord:
    return ActionRecord(
        round=rnd,
        turn=turn,
        actor_id=actor,
        action=action,
        target_id=target,
        message="",
        points_delta=0,
        round_score_after=score,
        was_defaulted=False,
    )


# --- temperature ---


def test_temperature_cooperative() -> None:
    actions = [act(1, 1, "A", "HELP", "B"), act(1, 1, "B", "HELP", "A"), act(1, 2, "A", "HELP", "B")]
    sig = compute_board_signals([player("A"), player("B")], actions, 1)
    assert sig.temperature_label == "cooperative"
    assert sig.cooperation_temperature == 1.0


def test_temperature_hostile() -> None:
    actions = [act(1, 1, "A", "HURT", "B"), act(1, 1, "B", "HURT", "A"), act(1, 2, "A", "HURT", "B")]
    sig = compute_board_signals([player("A"), player("B")], actions, 1)
    assert sig.temperature_label == "hostile"


def test_temperature_empty_is_mixed() -> None:
    actions = [act(1, 1, "A", "HOARD"), act(1, 1, "B", "HOARD")]
    sig = compute_board_signals([player("A"), player("B")], actions, 1)
    assert sig.temperature_label == "mixed"
    assert sig.cooperation_temperature == 0.5


# --- alliances ---


def test_mutual_help_pair_is_alliance() -> None:
    actions = [
        act(1, 1, "A", "HELP", "B"),
        act(1, 2, "A", "HELP", "B"),
        act(1, 1, "B", "HELP", "A"),
        act(1, 2, "B", "HELP", "A"),
    ]
    alliances = detect_alliances(actions)
    assert len(alliances) == 1
    assert alliances[0].members == ["A", "B"]
    assert alliances[0].strength == 4


def test_alliance_cluster_links_transitively() -> None:
    actions = [
        act(1, 1, "A", "HELP", "B"),
        act(1, 2, "A", "HELP", "B"),
        act(1, 1, "B", "HELP", "A"),
        act(1, 2, "B", "HELP", "A"),
        act(1, 1, "B", "HELP", "C"),
        act(1, 2, "B", "HELP", "C"),
        act(1, 1, "C", "HELP", "B"),
        act(1, 2, "C", "HELP", "B"),
    ]
    alliances = detect_alliances(actions)
    assert len(alliances) == 1
    assert alliances[0].members == ["A", "B", "C"]


def test_one_directional_help_is_not_alliance() -> None:
    actions = [act(1, 1, "A", "HELP", "B"), act(1, 2, "A", "HELP", "B")]
    assert detect_alliances(actions) == []


# --- surging ---


def test_surging_detects_rank_climb() -> None:
    players = [player("me", 5), player("A", 12), player("B", 9), player("C", 20)]
    actions = [
        act(1, 1, "me", "HOARD", score=4),
        act(1, 1, "A", "HOARD", score=10),
        act(1, 1, "B", "HOARD", score=8),
        act(1, 1, "C", "HOARD", score=2),
        act(1, 4, "me", "HOARD", score=5),
        act(1, 4, "A", "HOARD", score=12),
        act(1, 4, "B", "HOARD", score=9),
        act(1, 4, "C", "HOARD", score=20),
    ]
    assert detect_surging(players, actions) == ["C"]


def test_surging_empty_when_one_turn() -> None:
    players = [player("A", 4), player("B", 2)]
    actions = [act(1, 1, "A", "HOARD", score=4), act(1, 1, "B", "HOARD", score=2)]
    assert detect_surging(players, actions) == []


# --- pattern breaks ---


def test_pattern_break_flagged() -> None:
    actions = [
        act(1, 1, "A", "HOARD"),
        act(1, 2, "A", "HOARD"),
        act(1, 3, "A", "HOARD"),
        act(1, 4, "A", "HURT", "B"),  # broke the hoarding pattern
    ]
    assert detect_pattern_breaks(actions) == ["A"]


def test_no_pattern_break_when_consistent() -> None:
    actions = [act(1, 1, "A", "HOARD"), act(1, 2, "A", "HOARD"), act(1, 3, "A", "HOARD")]
    assert detect_pattern_breaks(actions) == []


# --- new alliance flag ---


def test_alliance_formed_flag() -> None:
    # Mutual help completes on turn 2 → alliance signature changes vs turn 1.
    actions = [
        act(1, 1, "A", "HELP", "B"),
        act(1, 1, "B", "HELP", "A"),
        act(1, 2, "A", "HELP", "B"),
        act(1, 2, "B", "HELP", "A"),
    ]
    assert alliance_formed_this_turn(actions, 1) is True
