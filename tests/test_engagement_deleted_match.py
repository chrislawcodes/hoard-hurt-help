"""A play that happened must stay counted, even if its match row is missing.

Regression tests for a bug found by driving the real page in a browser: it showed
"played a turn: 2" and "genuine turns: 0" at the same time.

The cause is a **disagreement between two definitions of the same thing**. The
migration that reconstructs history counts a genuine turn by looking at the
submission and its player. The page's query also joins through the turn to the
match — it needs the match only to check the name against the smoke-test prefix.
When the match row is missing, that inner join silently dropped the submission,
so one number counted it and the other did not.

Missing match rows are real: the dev database has 27 turns pointing at a match id
with no match row. Note this is NOT what `delete_match` produces — that deletes
submissions, messages, turns and players before the match, leaving nothing
orphaned. These came from scratch data. Either way the page must not contradict
itself, and a match we cannot see cannot be a smoke test, so its turns are kept.

`count_genuine_turns` had no test at all before this file, which is why the
disagreement reached production.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update

from app.models.match import GameState
from app.models.turn import Turn
from app.models.user import User
from app.read_models.engagement_milestones import (
    count_genuine_turns,
    load_engagement_report,
)
from tests.factories import add_submission, make_match, make_turn, seat_player

UTC = timezone.utc


async def _orphan_the_turns(db, match_id: str) -> None:
    """Point this match's turns at a match id that does not exist.

    Foreign keys are enforced in tests (app/db.py sets the pragma to match
    production), so the inconsistent state has to be created with them briefly
    off — which is presumably how it arose in the dev database too.
    """
    await db.execute(text("PRAGMA foreign_keys=OFF"))
    await db.execute(
        update(Turn).where(Turn.match_id == match_id).values(match_id="M_GONE")
    )
    await db.commit()
    await db.execute(text("PRAGMA foreign_keys=ON"))


async def _played(db, *, match_name: str, match_id: str = "M_TEST1"):
    """One user with one genuine move in one match. Returns the player."""
    await make_match(db, match_id, state=GameState.COMPLETED, name=match_name)
    turn = await make_turn(db, match_id)
    player = await seat_player(db, match_id, "Seat One")
    await add_submission(
        db, turn, player, submitted_at=datetime.now(UTC), was_defaulted=False
    )
    await db.commit()
    return player


@pytest.mark.asyncio
async def test_a_genuine_turn_is_counted(db) -> None:
    """The baseline. Without this, the regression test below proves nothing."""
    await _played(db, match_name="Prompt Live")
    assert await count_genuine_turns(db) == 1


@pytest.mark.asyncio
async def test_a_turn_counts_even_when_its_match_row_is_missing(db) -> None:
    """THE REGRESSION.

    The person played. The match row is gone. The page must still say they played,
    rather than quietly reporting zero next to a milestone that says otherwise.
    """
    await _played(db, match_name="Prompt Live")
    assert await count_genuine_turns(db) == 1, "precondition"

    await _orphan_the_turns(db, "M_TEST1")

    assert await count_genuine_turns(db) == 1, (
        "a missing match row must not delete the evidence that someone played"
    )


@pytest.mark.asyncio
async def test_smoke_test_matches_are_still_excluded(db) -> None:
    """The only reason the join exists. Making it outer must not lose this."""
    await _played(db, match_name="prod smoke check", match_id="M_SMOKE")
    assert await count_genuine_turns(db) == 0


@pytest.mark.asyncio
async def test_a_defaulted_turn_is_still_not_counted(db) -> None:
    """A missed deadline writes a submission row; it is not a move."""
    await make_match(db, "M_TEST2", state=GameState.COMPLETED, name="Prompt Live")
    turn = await make_turn(db, "M_TEST2")
    player = await seat_player(db, "M_TEST2", "Seat One")
    await add_submission(
        db, turn, player, submitted_at=datetime.now(UTC), was_defaulted=True
    )
    await db.commit()

    assert await count_genuine_turns(db) == 0


@pytest.mark.asyncio
async def test_returning_survives_a_missing_match_row(db) -> None:
    """The same helper feeds "came back another day", so it had the same bug."""
    await make_match(db, "M_TEST3", state=GameState.COMPLETED, name="Prompt Live")
    player = await seat_player(db, "M_TEST3", "Seat One")
    for days_ago, round_number in ((0, 1), (3, 2)):
        turn = await make_turn(db, "M_TEST3", round=round_number)
        await add_submission(
            db,
            turn,
            player,
            submitted_at=datetime.now(UTC) - timedelta(days=days_ago),
            was_defaulted=False,
        )
    await db.commit()

    report = await load_engagement_report(db, zone=UTC)
    assert report.returned_count == 1, "precondition: two days of play"

    await _orphan_the_turns(db, "M_TEST3")

    report = await load_engagement_report(db, zone=UTC)
    assert report.returned_count == 1, (
        "a missing match row must not erase that this person came back"
    )


@pytest.mark.asyncio
async def test_internal_accounts_are_still_excluded(db) -> None:
    """The outer join must not accidentally widen who is counted."""
    player = await _played(db, match_name="Prompt Live")
    assert await count_genuine_turns(db) == 1, "precondition"

    owner = (
        await db.execute(select(User).where(User.id == player.user_id))
    ).scalar_one()
    owner.is_internal = True
    await db.commit()

    assert await count_genuine_turns(db) == 0
