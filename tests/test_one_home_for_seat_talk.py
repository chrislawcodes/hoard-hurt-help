"""What a seat said on a turn must have exactly one answer.

Four places answered it and one of them answered differently. The viewer, the
live agent view and `load_action_records` all read the talk row and fell back to
the move's own message column for matches played before the talk phase existed
(migration 0013). The match export skipped the talk row entirely and read only
the move column.

So M_7975 exported three of its eight seats as silent across all 35 of their
moves while the viewer showed them talking the whole match. Nothing was lost —
the export was reading the wrong column, and the column it read holds whatever
an MCP agent puts in `submit_action`'s undocumented `message` argument.

Now `app/seat_talk.py` holds the rule and everything calls it, and the export
column is named `talk` for the thing it holds rather than `message` for its
shape. `scripts/match_runner/export_talk.py` holds the matching reader-side
rule, because exports written before the rename still say `message` and files on
disk do not rewrite themselves.

These tests are the guard: they fail if a second answer grows back, or if the
two sides of the export boundary stop agreeing on the column name.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.agent_play_reads import _load_public_action_records, load_match_players
from app.models import GameState, Match, TurnMessage
from app.read_models.match_export import (
    EXPORT_COLUMNS,
    ExportViewer,
    gather_export_rows,
)
from app.read_models.matches import load_action_records, load_match_timeline
from app.seat_talk import seat_talk_text
from tests.factories import add_submission, make_turn, seat_player

ADMIN = ExportViewer(user_id=None, is_platform_admin=True)


def test_a_missing_talk_row_falls_back_to_the_move() -> None:
    """No talk row at all: a pre-talk-phase match keeps its words."""
    assert seat_talk_text(None, "legacy words") == "legacy words"


def test_an_empty_talk_row_is_an_answer_not_a_missing_one() -> None:
    """Spoke and said nothing BEATS the move column, and must not fall through.

    `finalize_talk_phase` materializes an empty talk row for every active player,
    so this is the normal shape of a quiet modern turn — not a gap to paper over
    with whatever the move column happens to hold.
    """
    assert seat_talk_text("", "stray act-phase text") == ""


async def _match(db: AsyncSession) -> Match:
    match = Match(
        id="M_TALK",
        name="Seat Talk",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
    )
    db.add(match)
    await db.flush()
    return match


async def _three_kinds_of_seat(db: AsyncSession) -> Match:
    """One turn holding each shape the rule has to tell apart.

    Talkative: a real talk row PLUS a stray move message, which is what an MCP
    agent sends because `submit_action` requires a `message` argument the agent
    prompt never asks for. The talk row is the answer.

    Quiet: an empty talk row and the same stray move message. Still the talk row.

    Legacy: no talk row at all, the way every match before migration 0013 looks.
    """
    match = await _match(db)
    talkative = await seat_player(db, match.id, "Talkative", i=1)
    quiet = await seat_player(db, match.id, "Quiet", i=2)
    legacy = await seat_player(db, match.id, "Legacy", i=3)
    turn = await make_turn(db, match.id, turn_token="tk")

    db.add(
        TurnMessage(
            turn_id=turn.id,
            player_id=talkative.id,
            text="I will repay Quiet no matter what",
            thinking="",
            was_defaulted=False,
            submitted_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        TurnMessage(
            turn_id=turn.id,
            player_id=quiet.id,
            text="",
            thinking="",
            was_defaulted=True,
            submitted_at=None,
        )
    )
    for player in (talkative, quiet):
        await add_submission(
            db, turn, player, action="HOARD", message="stray act-phase text"
        )
    await add_submission(db, turn, legacy, action="HOARD", message="legacy words")
    await db.commit()
    return match


async def test_the_export_reports_talk_not_the_move_column(db: AsyncSession) -> None:
    """The bug this was written for: a talking seat exporting as silent."""
    match = await _three_kinds_of_seat(db)

    said = {row["agent_id"]: row["talk"] for row in await gather_export_rows(
        db, match.id, viewer=ADMIN
    )}

    assert said["Talkative"] == "I will repay Quiet no matter what"
    assert said["Quiet"] == ""
    assert said["Legacy"] == "legacy words"


async def test_every_reader_gives_the_same_answer(db: AsyncSession) -> None:
    """Export, action records, timeline and the live agent view must agree.

    They read through different queries — the export and records off the shared
    timeline, the live view off its own windowed query — so agreement is not
    structural and nothing but this test holds them together.
    """
    match = await _three_kinds_of_seat(db)

    from_export = {
        row["agent_id"]: row["talk"]
        for row in await gather_export_rows(db, match.id, viewer=ADMIN)
    }
    from_records = {r.actor_id: r.message for r in await load_action_records(db, match.id)}
    from_timeline = {
        action.agent_id: action.message
        for turn in await load_match_timeline(db, match.id)
        for action in turn.actions
    }
    players = await load_match_players(db, match.id)
    from_live = {
        r.actor_id: r.message
        for r in await _load_public_action_records(db, match.id, players)
    }

    assert from_export == from_records == from_timeline == from_live


# --- The export boundary: what the writer names, the reader must look for ---

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "match_runner"


def _load_export_talk():
    """Import the reader as a module. It is a script, not a package member."""
    spec = importlib.util.spec_from_file_location(
        "export_talk", RUNNER / "export_talk.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_talk"] = module
    spec.loader.exec_module(module)
    return module


def test_the_reader_looks_for_the_column_the_export_writes() -> None:
    """The two sides of the export boundary must agree on the name.

    Renaming the column without teaching the reader would report every new match
    as silent — the same bug as before, one layer out. Nothing else pins these
    two files together.
    """
    export_talk = _load_export_talk()
    assert export_talk.TALK_KEYS[0] in EXPORT_COLUMNS, (
        f"the export writes {EXPORT_COLUMNS!r} and the reader looks for "
        f"{export_talk.TALK_KEYS!r}; its preferred key is not among the columns"
    )


def test_the_reader_still_understands_a_pre_rename_export() -> None:
    """Exports already on disk say `message` and never get rewritten."""
    export_talk = _load_export_talk()
    assert export_talk.read_talk({"message": "legacy words"}) == "legacy words"


def test_the_reader_prefers_the_current_name() -> None:
    export_talk = _load_export_talk()
    row = {"talk": "current words", "message": "stale words"}
    assert export_talk.read_talk(row) == "current words"


def test_a_null_talk_reads_as_empty_not_as_a_crash() -> None:
    """The CSV path writes "" but JSON can carry null; both mean "said nothing"."""
    export_talk = _load_export_talk()
    assert export_talk.read_talk({"talk": None}) == ""


def test_a_row_with_neither_column_fails_loudly() -> None:
    """Silence here would print a confident 0% built on nothing."""
    export_talk = _load_export_talk()
    with pytest.raises(KeyError, match="no talk column"):
        export_talk.read_talk({"action": "HOARD"})
