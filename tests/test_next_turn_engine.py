"""Unit tests for the pure next-turn selector (no DB)."""

from datetime import datetime, timedelta, timezone

from app.engine.next_turn import TurnCandidate, select_next_turn

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _c(match_id: str, deadline_offset: int, round_: int = 1, turn: int = 1) -> TurnCandidate:
    return TurnCandidate(
        match_id=match_id,
        round=round_,
        turn=turn,
        deadline=_NOW + timedelta(seconds=deadline_offset),
    )


def test_returns_none_when_empty() -> None:
    assert select_next_turn([]) is None


def test_picks_nearest_deadline() -> None:
    chosen = select_next_turn([_c("G_0002", 60), _c("G_0001", 10), _c("G_0003", 30)])
    assert chosen is not None
    assert chosen.match_id == "G_0001"


def test_ties_break_by_game_then_round_then_turn() -> None:
    # All share a deadline; order is decided by match_id, then round, then turn.
    a = _c("G_0002", 10, round_=1, turn=1)  # later game id
    b = _c("G_0001", 10, round_=5, turn=9)  # same game, later round
    c = _c("G_0001", 10, round_=1, turn=2)  # the winner
    assert select_next_turn([a, b, c]) == c


def test_single_candidate() -> None:
    only = _c("G_0007", 5)
    assert select_next_turn([only]) == only
