"""The replay must reproduce the match it replays.

This is the one property the replay tool rests on. If replaying a recorded match
with nothing changed does not give back the recorded result, then no what-if run
on top of it means anything — the difference you are reading could be your
change, or could be the replay.

Two forms of one rule live here, which is why this test exists (CLAUDE.md, "Two
forms need a test"): the live match scored through the turn loop, and the replay
seeding that same history and handing the rest back to that loop. They are meant
to be the same code, and this asserts it end to end rather than in principle.

RUN AS A SUBPROCESS, DELIBERATELY. `run_replay` repoints DATABASE_URL and drops
every loaded `app.*` module so the offline engine binds to its own SQLite file.
That is correct for a script that owns its process and poison inside pytest,
where the suite shares one: calling it in-process failed two or three unrelated
tests per run, a different set each time, because whichever test landed after it
inherited the replay's database. Shelling out also tests what actually ships.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY = REPO_ROOT / "scripts" / "match_runner" / "replay_match.py"


def _fixture_export() -> dict:
    """A tiny two-round match: help, hurt and hoard, with everyone talking."""
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
        "players": [{"agent_id": str(i), "strategy_prompt": f"Strategy: {s}.", "model": "none"}
                    for i, s in enumerate(seats)],
        "submissions": subs,
    }


def _run(export_path: Path, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(export_path), *args],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=600,
    )
    assert proc.returncode == 0, f"replay exited {proc.returncode}:\n{proc.stderr}"
    return proc.stdout


def _scored(export: dict, tmp_path: Path) -> dict:
    """Fill the fixture's per-turn scores in by asking the engine for them.

    The fixture cannot carry hand-written `round_score_after` values: writing
    them by hand would copy the payoff table into this file, which is the
    duplication the tool exists to avoid. So the engine scores the moves once and
    its answer becomes the "recorded" match. That makes this a check that the
    replay is stable and that its comparison works; the check that it agrees with
    LIVE play is the sweep over real exports, which cannot live in the repo.
    """
    path = tmp_path / "unscored.json"
    path.write_text(json.dumps(export))
    db = tmp_path / "score.sqlite3"
    _run(path, "--from", "R99T1", "--db", str(db))

    con = sqlite3.connect(db)
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


def test_seeding_every_recorded_move_reproduces_the_recorded_result(tmp_path: Path) -> None:
    """With every turn seeded and none replayed, the result must be unchanged."""
    export = tmp_path / "M_TEST.json"
    export.write_text(json.dumps(_scored(_fixture_export(), tmp_path)))

    out = _run(export, "--from", "R99T1", "--db", str(tmp_path / "replay.sqlite3"))

    assert "reproduces the recorded result exactly." in out, out
    assert "replaying the remaining 0" in out, "nothing was left to replay"


def test_resumed_turns_get_a_talk_phase(tmp_path: Path) -> None:
    """Turns replayed forward must be played the way the game plays them.

    The talk phase is the reason this hands off to the shipped loop instead of
    driving turns itself. A replay that skipped it would produce a table that
    cannot speak — a different game, in one whose pacts are made in chat — and
    nothing in the standings would show it.
    """
    export = tmp_path / "M_TEST.json"
    export.write_text(json.dumps(_scored(_fixture_export(), tmp_path)))
    db = tmp_path / "resume.sqlite3"

    _run(export, "--from", "R2T1", "--db", str(db))

    con = sqlite3.connect(db)
    talk = dict(con.execute(
        "select t.round, count(m.id) from turns t "
        "left join turn_messages m on m.turn_id = t.id group by t.round"
    ).fetchall())
    con.close()
    assert talk.get(1, 0) == 0, "seeded rounds carry their chat on the moves, not as new messages"
    assert talk.get(2, 0) > 0, "replayed rounds must have gone through the talk phase"


def test_a_replayed_seat_can_be_swapped(tmp_path: Path) -> None:
    """--seat must actually change who plays a seat, and say so in the output."""
    export = tmp_path / "M_TEST.json"
    export.write_text(json.dumps(_scored(_fixture_export(), tmp_path)))

    out = _run(
        export, "--from", "R2T1", "--seat", "Charlie=bot:grudger",
        "--db", str(tmp_path / "swap.sqlite3"),
    )

    assert "Charlie" in out and "bot: grudger" in out, out
    assert "bot: pragmatist" in out, "unnamed seats should keep the default"


def test_it_refuses_a_model_seat_it_cannot_match_to_a_playbook(tmp_path: Path) -> None:
    """Rather than hand a model somebody else's strategy, it must stop.

    The export's two halves do not join, so a seat's strategy is recovered by
    matching the preset name. When that fails the only safe answer is to refuse:
    a model playing the wrong playbook would look exactly like a real result.
    """
    export_data = _scored(_fixture_export(), tmp_path)
    for player in export_data["players"]:
        player["strategy_prompt"] = "Strategy: Nothing That Matches A Seat."
    export = tmp_path / "M_NOMATCH.json"
    export.write_text(json.dumps(export_data))

    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(export), "--from", "R2T1",
         "--seat", "Alpha=model:claude-haiku-4-5", "--db", str(tmp_path / "no.sqlite3")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=600,
    )
    assert proc.returncode != 0
    assert "could not be matched" in proc.stdout + proc.stderr
