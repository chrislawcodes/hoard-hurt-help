"""The replay must reproduce the match it replays.

This is the one property the replay tool rests on. If replaying a recorded match
with nothing changed does not give back the recorded result, then no what-if run
on top of it means anything — the difference you are reading could be your
change, or could be the replay.

Two forms of one rule live here, which is why this test exists (CLAUDE.md, "Two
forms need a test"): the live match scored through the turn loop, and the replay
scoring the same moves offline. They share `resolve_turn` and `round_award`, and
this asserts the sharing holds end to end rather than in principle.

RUN AS A SUBPROCESS, DELIBERATELY. `run_replay` repoints DATABASE_URL and drops
every loaded `app.*` module so the offline engine binds to its own SQLite file.
That is correct for a script that owns its process and poison inside pytest,
where the suite shares one: calling it in-process failed two or three unrelated
tests per run, a different pair each time, because whichever test landed after
it got the replay's database. Shelling out also tests what actually ships.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY = REPO_ROOT / "scripts" / "match_runner" / "replay_match.py"


def _fixture_export() -> dict:
    """A tiny two-round match: help, hurt, hoard, and a round that ties."""
    seats = ["Alpha", "Bravo", "Charlie"]
    moves = [
        ("Alpha", "HELP", "Bravo"), ("Bravo", "HELP", "Alpha"), ("Charlie", "HOARD", None),
        ("Alpha", "HELP", "Bravo"), ("Bravo", "HELP", "Alpha"), ("Charlie", "HURT", "Alpha"),
        ("Alpha", "HELP", "Bravo"), ("Bravo", "HELP", "Alpha"), ("Charlie", "HURT", "Bravo"),
        ("Alpha", "HOARD", None), ("Bravo", "HOARD", None), ("Charlie", "HOARD", None),
    ]
    subs = []
    for i, (seat, action, target) in enumerate(moves):
        rnd, turn = divmod(i // len(seats), 2)
        subs.append({
            "agent_id": seat, "action": action, "target_id": target,
            "round": rnd + 1, "turn": turn + 1, "round_score_after": 0,
            "thinking": "", "message": f"{seat} speaks", "was_defaulted": False,
        })
    from app.games.hoard_hurt_help.rules import RULES_VERSION

    return {
        # Stamp today's version: the replay warns when the recorded rules differ
        # from the code's, and this fixture is about reproduction, not rescoring.
        "game": {"id": "M_TEST", "name": "replay fixture", "rules_version": RULES_VERSION},
        "players": [{"agent_id": str(i), "strategy_prompt": f"Strategy: {s}."}
                    for i, s in enumerate(seats)],
        "submissions": subs,
    }


def _run(export_path: Path, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(export_path), *args],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=300,
    )
    assert proc.returncode == 0, f"replay exited {proc.returncode}:\n{proc.stderr}"
    return proc.stdout


def _scored(export: dict, tmp_path: Path) -> dict:
    """Fill the fixture's per-turn scores in by asking the engine for them.

    The fixture cannot carry hand-written `round_score_after` values: writing
    them by hand would mean copying the payoff table into this file, which is
    the duplication the whole tool is built to avoid. So the engine scores the
    moves once and its answer becomes the "recorded" match. What that turns this
    into is a check that the replay is stable and that its comparison works —
    the check that it agrees with LIVE play is the sweep over real exports,
    which cannot live in the repo.
    """
    path = tmp_path / "unscored.json"
    path.write_text(json.dumps(export))
    _run(path, "--from", "R99T1", "--db", str(tmp_path / "score.sqlite3"))

    import sqlite3

    con = sqlite3.connect(tmp_path / "score.sqlite3")
    rows = con.execute(
        "select t.round, t.turn, p.seat_name, ts.round_score_after "
        "from turn_submissions ts join turns t on t.id = ts.turn_id "
        "join players p on p.id = ts.player_id"
    ).fetchall()
    con.close()
    by = {(r, t, seat): score for r, t, seat, score in rows}
    for sub in export["submissions"]:
        sub["round_score_after"] = by[(sub["round"], sub["turn"], sub["agent_id"])]
    return export


def test_replaying_every_recorded_move_reproduces_the_recorded_result(tmp_path: Path) -> None:
    """With no seat overridden, the replay must land exactly where the match did."""
    export = tmp_path / "M_TEST.json"
    export.write_text(json.dumps(_scored(_fixture_export(), tmp_path)))

    out = _run(export, "--from", "R99T1", "--db", str(tmp_path / "replay.sqlite3"))

    assert "reproduces the recorded result exactly." in out, out
    assert "played 0 forward" in out, "every turn was in the log, so none should be new"


def test_a_scripted_seat_changes_the_outcome_and_says_so(tmp_path: Path) -> None:
    """An overridden seat must actually play differently, and be flagged.

    Without this, a replay that quietly ignored `--seat` would still print
    "reproduces exactly" and read as a successful what-if.
    """
    export = tmp_path / "M_TEST.json"
    export.write_text(json.dumps(_fixture_export()))

    out = _run(
        export, "--from", "R2T1", "--seat", "Charlie=always:HELP",
        "--db", str(tmp_path / "what-if.sqlite3"),
    )

    assert "always HELP" in out, out
    assert "R2T1  Charlie             HELP" in out, out
    # The caveat that the other seats are not reacting must always ride along.
    assert "do not" in out and "react to your change" in out, out


def test_it_refuses_to_invent_moves_past_the_end_of_the_log(tmp_path: Path) -> None:
    """A seat with no recorded move and no driver must fail loudly.

    Silently defaulting is exactly the fabrication the tool exists to undo: a
    default scores as a HOARD and lands in the measured data looking like a
    decision.
    """
    # Keep both rounds, but lose one seat's round-two moves — the shape a
    # truncated or partly-exported log actually has.
    gappy = _fixture_export()
    gappy["submissions"] = [
        s for s in gappy["submissions"]
        if not (s["agent_id"] == "Bravo" and s["round"] == 2)
    ]
    export = tmp_path / "M_SHORT.json"
    export.write_text(json.dumps(gappy))

    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(export), "--from", "R2T1",
         "--seat", "Alpha=always:HOARD", "--db", str(tmp_path / "short.sqlite3")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=300,
    )
    assert proc.returncode != 0
    assert "no recorded move" in proc.stdout + proc.stderr
