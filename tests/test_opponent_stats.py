"""Unit tests for the per-opponent stats + short-list selection engine."""

from __future__ import annotations

from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.opponent_stats import MAX_SHORTLIST, build_opponent_view


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
    score: int = 0,
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
        round_score_after=score,
        was_defaulted=defaulted,
    )


def test_toward_you_tallies() -> None:
    players = [player("me", 4), player("A", 8), player("B", 2)]
    actions = [
        act(1, 1, "A", "HELP", "me"),
        act(1, 2, "A", "HELP", "me"),
        act(1, 2, "B", "HURT", "me"),
    ]
    stats, _ = build_opponent_view("me", players, actions, set())
    by_id = {s.agent_id: s for s in stats}
    assert by_id["A"].helped_you == 2
    assert by_id["A"].hurt_you == 0
    assert by_id["B"].hurt_you == 1


def test_reciprocity_next_turn_mirror() -> None:
    players = [player("me"), player("B")]
    actions = [
        act(1, 1, "me", "HELP", "B"),
        act(1, 2, "B", "HELP", "me"),  # mirrored the very next turn
    ]
    stats, _ = build_opponent_view("me", players, actions, set())
    b = next(s for s in stats if s.agent_id == "B")
    assert b.returned_help is True
    assert b.returned_hurt is False


def test_reciprocity_not_next_turn_is_false() -> None:
    players = [player("me"), player("B")]
    actions = [
        act(1, 1, "me", "HELP", "B"),
        act(1, 2, "B", "HOARD"),
        act(1, 3, "B", "HELP", "me"),  # returned, but not the immediate next turn
    ]
    stats, _ = build_opponent_view("me", players, actions, set())
    b = next(s for s in stats if s.agent_id == "B")
    assert b.returned_help is False


def test_style_mix_percentages() -> None:
    players = [player("me"), player("A")]
    actions = [
        act(1, 1, "A", "HOARD"),
        act(1, 2, "A", "HOARD"),
        act(1, 3, "A", "HELP", "me"),
        act(1, 4, "A", "HURT", "me"),
    ]
    stats, _ = build_opponent_view("me", players, actions, set())
    a = next(s for s in stats if s.agent_id == "A")
    assert a.style.hoard_pct == 50
    assert a.style.help_pct == 25
    assert a.style.hurt_pct == 25


def test_shortlist_capped_at_max() -> None:
    players = [player("me", 50)] + [player(f"B{i:02d}", i) for i in range(30)]
    stats, aggregate = build_opponent_view("me", players, [], set())
    assert len(stats) <= MAX_SHORTLIST
    assert aggregate is not None
    # 30 opponents, at most MAX_SHORTLIST shown → the rest are folded.
    assert aggregate.count == 30 - len(stats)


def test_selection_reasons() -> None:
    # me is mid-table. Top-3 by score are threats; N sits just above me (neighbor,
    # not a top-3 threat); X interacted with me last turn.
    players = [
        player("me", 50),
        player("T1", 100),
        player("T2", 90),
        player("T3", 80),
        player("N", 51),
        player("X", 49),
        player("low", 5),
    ]
    actions = [act(1, 1, "X", "HURT", "me")]
    stats, _ = build_opponent_view("me", players, actions, set())
    reasons = {s.agent_id: s.reason for s in stats}
    assert reasons["X"] == "interacted"
    assert reasons["T1"] == "threat"
    assert reasons["N"] == "neighbor"


def test_flagged_opponent_forced_in() -> None:
    players = [player("me", 50)] + [player(f"B{i:02d}", 0) for i in range(30)]
    stats, _ = build_opponent_view("me", players, [], {"B29"})
    assert any(s.agent_id == "B29" and s.reason == "flagged" for s in stats)


def test_no_opponents_returns_empty() -> None:
    stats, aggregate = build_opponent_view("me", [player("me")], [], set())
    assert stats == []
    assert aggregate is None


def test_tiny_game_no_aggregate() -> None:
    players = [player("me", 5), player("A", 3), player("B", 1)]
    stats, aggregate = build_opponent_view("me", players, [], set())
    assert {s.agent_id for s in stats} == {"A", "B"}
    assert aggregate is None  # everyone fit on the short-list


def test_defaulted_action_still_counts_in_style() -> None:
    players = [player("me"), player("A")]
    actions = [act(1, 1, "A", "HOARD", defaulted=True)]
    stats, _ = build_opponent_view("me", players, actions, set())
    a = next(s for s in stats if s.agent_id == "A")
    assert a.style.hoard_pct == 100
