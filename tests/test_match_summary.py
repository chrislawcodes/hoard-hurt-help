"""Unit tests for the end-of-game finale builder (a pure function over data)."""

from __future__ import annotations

from typing import Any

from app.games.hoard_hurt_help.match_summary import build_final_summary


def _row(
    seat: str,
    *,
    round_wins: float,
    is_bot: bool = False,
    provider: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    return {
        "agent_id": seat,
        "display_name": seat,
        "round_wins": round_wins,
        "is_bot": is_bot,
        "provider": provider,
        "owner_handle": owner,
    }


def _act(
    seat: str,
    action: str,
    *,
    target: str | None = None,
    delta: int = 0,
    mutual: bool = False,
) -> dict[str, Any]:
    return {
        "agent_id": seat,
        "action": action,
        "target_id": target,
        "display_delta": delta,
        "mutual": mutual,
    }


def _turn(rnd: int, turn: int, actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"round": rnd, "turn": turn, "actions": actions}


def test_standings_ordered_by_rounds_then_points() -> None:
    summary = build_final_summary(
        total_rounds=3,
        scoreboard=[
            _row("Dido", round_wins=1, provider="Gemini"),
            _row("Cleopatra", round_wins=2, provider="Claude"),
            _row("Hannibal", round_wins=0),
        ],
        total_scores={"Cleopatra": 38, "Dido": 34, "Hannibal": 29},
        history=[],
        winner_seat="Cleopatra",
    )
    assert summary is not None
    ranks = [(r["display_name"], r["rank"]) for r in summary["standings"]]
    assert ranks == [("Cleopatra", 1), ("Dido", 2), ("Hannibal", 3)]
    assert summary["champion"]["display_name"] == "Cleopatra"
    assert summary["champion_decided_by_points"] is False
    # Whole round-win totals display as plain integers.
    assert summary["champion"]["round_wins_label"] == "2"
    # Divider sits before the first zero-round seat (which is below a winner).
    assert summary["first_zero_rank"] == 3


def test_points_break_a_tie_on_rounds() -> None:
    summary = build_final_summary(
        total_rounds=2,
        scoreboard=[
            _row("A", round_wins=1),
            _row("B", round_wins=1),
        ],
        total_scores={"A": 20, "B": 31},
        history=[],
        winner_seat="B",
    )
    assert summary is not None
    assert [r["display_name"] for r in summary["standings"]] == ["B", "A"]
    assert summary["champion"]["display_name"] == "B"
    # Both tied on rounds won → the win was decided on points.
    assert summary["champion_decided_by_points"] is True


def test_winner_seat_overrides_sort_for_champion() -> None:
    # Even if the rule-sort would top someone else, the engine's recorded winner
    # is the champion (they should agree, but the engine is the source of truth).
    summary = build_final_summary(
        total_rounds=1,
        scoreboard=[_row("A", round_wins=1), _row("B", round_wins=1)],
        total_scores={"A": 10, "B": 5},
        history=[],
        winner_seat="B",
    )
    assert summary is not None
    assert summary["champion"]["display_name"] == "B"


def test_action_mix_percentages_sum_to_100() -> None:
    history = [
        _turn(1, 1, [_act("A", "HOARD", delta=2), _act("B", "HELP", target="A", delta=4)]),
        _turn(1, 2, [_act("A", "HELP", target="B", delta=4), _act("B", "HURT", target="A", delta=-4)]),
        _turn(1, 3, [_act("A", "HURT", target="B", delta=-4), _act("B", "HOARD", delta=2)]),
    ]
    summary = build_final_summary(
        total_rounds=1,
        scoreboard=[_row("A", round_wins=1), _row("B", round_wins=0)],
        total_scores={"A": 6, "B": 2},
        history=history,
        winner_seat="A",
    )
    assert summary is not None
    for row in summary["standings"]:
        mix = row["mix"]
        assert mix["hoard"] + mix["help"] + mix["hurt"] == 100
    # A played 1 hoard / 1 help / 1 hurt → ~33 each (one slice carries rounding).
    a_mix = next(r["mix"] for r in summary["standings"] if r["display_name"] == "A")
    assert a_mix["hoard"] == 33 and a_mix["help"] == 33


def test_superlatives_surface_real_signal() -> None:
    history = [
        # A mutual pact between A and B (+8 each), twice.
        _turn(1, 1, [_act("A", "HELP", target="B", delta=8, mutual=True),
                     _act("B", "HELP", target="A", delta=8, mutual=True)]),
        _turn(1, 2, [_act("A", "HELP", target="B", delta=8, mutual=True),
                     _act("B", "HELP", target="A", delta=8, mutual=True)]),
        # C attacks twice.
        _turn(1, 3, [_act("C", "HURT", target="A", delta=-4),
                     _act("C", "HURT", target="B", delta=-4)]),
    ]
    summary = build_final_summary(
        total_rounds=1,
        scoreboard=[_row("A", round_wins=1), _row("B", round_wins=0), _row("C", round_wins=0)],
        total_scores={"A": 20, "B": 16, "C": 0},
        history=history,
        winner_seat="A",
    )
    assert summary is not None
    assert summary["quiet"] is False
    labels = {s["label"]: s["value"] for s in summary["stats"]}
    assert "Biggest turn" in labels and "+8" in labels["Biggest turn"]
    assert labels["Tightest pact"] == "A ↔ B · 2×"
    assert labels["Most ruthless"] == "C · 2 hurts"
    # Most generous = most HELP actions (A and B both helped twice; A wins the
    # alphabetical tiebreak).
    assert labels["Most generous"] == "A · 2 helps"


def test_quiet_all_hoard_match_has_no_stats() -> None:
    history = [
        _turn(1, 1, [_act("A", "HOARD", delta=2), _act("B", "HOARD", delta=2)]),
        _turn(1, 2, [_act("A", "HOARD", delta=2), _act("B", "HOARD", delta=2)]),
    ]
    summary = build_final_summary(
        total_rounds=1,
        scoreboard=[_row("A", round_wins=0.5), _row("B", round_wins=0.5)],
        total_scores={"A": 4, "B": 4},
        history=history,
        winner_seat="A",
    )
    assert summary is not None
    assert summary["quiet"] is True
    assert summary["stats"] == []
    # Everyone tied on rounds → decided on points; no zero-round divider since the
    # whole table shares the same (non-zero) round-win total.
    assert summary["champion_decided_by_points"] is True
    assert summary["first_zero_rank"] is None
    for row in summary["standings"]:
        assert row["mix"] == {"hoard": 100, "help": 0, "hurt": 0}
        # Tie-split fractions render to at most two decimals, never a raw float.
        assert row["round_wins_label"] == "0.5"


def test_fractional_round_wins_format_cleanly() -> None:
    summary = build_final_summary(
        total_rounds=3,
        scoreboard=[_row("A", round_wins=2 / 3), _row("B", round_wins=1 / 3)],
        total_scores={"A": 14, "B": 10},
        history=[],
        winner_seat="A",
    )
    assert summary is not None
    labels = {r["display_name"]: r["round_wins_label"] for r in summary["standings"]}
    assert labels == {"A": "0.67", "B": "0.33"}


def test_no_players_returns_none() -> None:
    assert build_final_summary(
        total_rounds=3, scoreboard=[], total_scores={}, history=[], winner_seat=None
    ) is None
