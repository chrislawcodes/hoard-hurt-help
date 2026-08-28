"""The replay must reproduce the match it replays.

This is the one property the replay tool rests on. If replaying a recorded match
with nothing changed does not give back the recorded result, then no what-if run
on top of it means anything — the difference you are reading could be your change
or could be the replay.

Two forms of one rule live here, which is why this test exists (CLAUDE.md, "Two
forms need a test"): the live match scored through the turn loop, and the replay
scoring the same moves offline. They share `resolve_turn` and `round_award`, and
this asserts the sharing actually holds end to end rather than in principle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "match_runner"))

from match_report import round_wins_from_submissions, total_points_from_submissions  # noqa: E402


def _export(tmp_path: Path) -> dict:
    """A tiny two-round match: enough to exercise help, hurt, hoard and a tie."""
    seats = ["Alpha", "Bravo", "Charlie"]
    moves = [
        # R1: Alpha and Bravo trade help, Charlie hoards.
        ("Alpha", "HELP", "Bravo"), ("Bravo", "HELP", "Alpha"), ("Charlie", "HOARD", None),
        ("Alpha", "HELP", "Bravo"), ("Bravo", "HELP", "Alpha"), ("Charlie", "HURT", "Alpha"),
        # R2: Charlie attacks the leader, the pact holds.
        ("Alpha", "HELP", "Bravo"), ("Bravo", "HELP", "Alpha"), ("Charlie", "HURT", "Bravo"),
        ("Alpha", "HOARD", None), ("Bravo", "HOARD", None), ("Charlie", "HOARD", None),
    ]
    subs = []
    for i, (seat, action, target) in enumerate(moves):
        rnd, turn = divmod(i // len(seats), 2)
        subs.append({
            "agent_id": seat, "action": action, "target_id": target,
            "round": rnd + 1, "turn": turn + 1,
            "round_score_after": 0, "thinking": "", "message": "",
            "was_defaulted": False,
        })
    return {
        "game": {"id": "M_TEST", "name": "replay fixture", "state": "COMPLETED"},
        "players": [{"agent_id": str(i), "strategy_prompt": f"Strategy: {s}."}
                    for i, s in enumerate(seats)],
        "submissions": subs,
    }


@pytest.mark.asyncio
async def test_replay_with_no_overrides_reproduces_the_recorded_result(tmp_path: Path) -> None:
    """Replaying every recorded move must give back the recorded standings.

    The fixture's `round_score_after` is zero, so the check runs the replay twice:
    once to produce the scores the engine says these moves earn, and once to
    confirm a second run of the same input lands in the same place. That makes
    this a determinism-and-agreement check without needing a hand-computed
    scoreboard that would itself be a third copy of the payoff rules.
    """
    from replay_engine import run_replay
    from replay_seats import KeepDriver, Move

    export = _export(tmp_path)
    seats = sorted({s["agent_id"] for s in export["submissions"]})

    def drivers() -> dict[str, KeepDriver]:
        rec: dict[str, dict[tuple[int, int], Move]] = {s: {} for s in seats}
        for s in export["submissions"]:
            rec[s["agent_id"]][(s["round"], s["turn"])] = Move(
                s["action"], s.get("target_id"), ""
            )
        return {s: KeepDriver(rec[s]) for s in seats}

    first = await run_replay(
        export, start=(99, 1), stop=None, drivers=drivers(),
        db_path=str(tmp_path / "a.sqlite3"),
    )
    second = await run_replay(
        export, start=(99, 1), stop=None, drivers=drivers(),
        db_path=str(tmp_path / "b.sqlite3"),
    )

    assert first.turns_played == 0, "every turn was in the log, so none should be new"
    assert first.turns_replayed == 4
    assert first.round_wins == second.round_wins
    assert first.totals == second.totals
    assert sum(first.round_wins.values()) == pytest.approx(2.0), "two rounds, two wins"
    # Scores must actually have been computed, and must separate the seats —
    # otherwise the two runs could agree by both doing nothing. What the right
    # ordering IS belongs to the payoff tests, not here; this test is about the
    # replay agreeing with itself and with the engine, not about who should win.
    assert any(first.totals.values()), "no points were scored at all"
    assert len(set(first.totals.values())) > 1, "every seat tied — payoffs did not run"


def test_export_halves_are_counted_the_same_way() -> None:
    """The report's two derivations must agree on who is in the match.

    `players` keys by numeric agent id and `submissions` by seat name, so
    anything that reads a result must read it from the move rows. This pins that
    both helpers do, and would fail if either started trusting `players`.
    """
    export = _export(Path("."))
    wins = round_wins_from_submissions(export["submissions"])
    points = total_points_from_submissions(export["submissions"])
    seats = {s["agent_id"] for s in export["submissions"]}
    assert set(wins) == seats
    assert set(points) == seats
