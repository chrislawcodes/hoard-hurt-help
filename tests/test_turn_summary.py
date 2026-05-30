"""Unit tests for the assembled TurnSummary."""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.turn_summary import build_turn_summary

DEADLINE = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
    msg: str = "",
    pts: int = 0,
    defaulted: bool = False,
) -> ActionRecord:
    return ActionRecord(
        round=rnd,
        turn=turn,
        actor_id=actor,
        action=action,
        target_id=target,
        message=msg,
        points_delta=pts,
        round_score_after=0,
        was_defaulted=defaulted,
    )


def test_first_turn_has_no_delta() -> None:
    players = [player("me"), player("A"), player("B")]
    summary = build_turn_summary("me", players, [], 1, 1, DEADLINE, "tok")
    assert summary.turn_delta is None
    assert summary.messages_for_you == []
    assert summary.your_situation.turn_token == "tok"
    assert summary.your_situation.current_round == 1
    assert summary.board_signals.temperature_label == "mixed"


def test_situation_and_rank() -> None:
    players = [player("me", 10), player("A", 20), player("B", 10)]
    summary = build_turn_summary("me", players, [], 1, 3, DEADLINE, "tok")
    # A(20) rank1; B and me tie at 10, "B" < "me" → B rank2, me rank3.
    assert summary.your_situation.rank == 3
    assert summary.your_situation.round_score == 10
    assert summary.standings_view.leaders[0].agent_id == "A"
    assert summary.standings_view.total_players == 3


def test_delta_involving_you_and_others_summary() -> None:
    players = [player("me", 10), player("A", 20), player("B", 10), player("C", 5), player("D", 0)]
    actions = [
        act(1, 2, "me", "HOARD", msg="mine"),
        act(1, 2, "A", "HURT", "me", msg="back off"),
        act(1, 2, "B", "HOARD", msg="all cooperate"),
        act(1, 2, "C", "HOARD", msg=""),
        act(1, 2, "D", "HOARD", msg="hi", defaulted=True),
    ]
    summary = build_turn_summary("me", players, actions, 1, 3, DEADLINE, "tok")
    assert summary.turn_delta is not None
    actors_involving = {d.actor_id for d in summary.turn_delta.involving_you}
    assert actors_involving == {"me", "A"}
    assert summary.turn_delta.others_summary == "3 hoarded, 0 helped, 1 hurt"


def test_messages_directed_and_broadcast() -> None:
    players = [player("me"), player("A"), player("B"), player("C"), player("D")]
    actions = [
        act(1, 1, "me", "HOARD", msg="mine"),
        act(1, 1, "A", "HURT", "me", msg="back off"),  # directed at me
        act(1, 1, "B", "HOARD", msg="all cooperate"),  # broadcast
        act(1, 1, "C", "HOARD", msg=""),  # empty → skipped
        act(1, 1, "D", "HOARD", msg="hi", defaulted=True),  # defaulted → skipped
    ]
    summary = build_turn_summary("me", players, actions, 1, 2, DEADLINE, "tok")
    directed = [m for m in summary.messages_for_you if not m.public]
    broadcasts = [m for m in summary.messages_for_you if m.public]
    assert [m.from_agent_id for m in directed] == ["A"]
    assert directed[0].on_action == "HURT"
    assert [m.from_agent_id for m in broadcasts] == ["B"]
    assert summary.flags.messages_for_you_count == 1


def test_large_game_keeps_aggregate() -> None:
    players = [player("me", 50)] + [player(f"B{i:02d}", i) for i in range(30)]
    summary = build_turn_summary("me", players, [], 1, 1, DEADLINE, "tok")
    assert summary.opponents_aggregate is not None
    assert len(summary.opponents) <= 12


def test_pattern_break_sets_flag_and_forces_opponent() -> None:
    players = [player("me", 5), player("A", 5)]
    actions = [
        act(1, 1, "A", "HOARD"),
        act(1, 2, "A", "HOARD"),
        act(1, 3, "A", "HOARD"),
        act(1, 4, "A", "HURT", "me"),  # break + targets me
    ]
    summary = build_turn_summary("me", players, actions, 1, 5, DEADLINE, "tok")
    assert "A" in summary.flags.pattern_breaks
