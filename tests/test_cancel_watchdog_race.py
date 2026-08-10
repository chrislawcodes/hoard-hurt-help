"""`cancel_match` must commit CANCELLED before it stops the scheduler task.

The order is the whole point. The scheduler watchdog runs every two seconds and
restarts any match still ACTIVE in the database with no running task — that is
how a crashed match heals itself. If cancel stops the task first, the match looks
exactly like a crashed one until the commit lands, so a watchdog pass landing in
that window resurrects the match that was just cancelled.

The end state is identical either way, so a test that only checks "is it
cancelled afterwards" passes against the bug. These tests assert the ORDER.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engine import match_deletion
from app.engine.match_deletion import cancel_match
from app.models.match import GameState, Match
from tests.factories import make_match


async def _seed(reset_db, match_id: str, state: GameState) -> None:
    async with reset_db() as db:
        await make_match(
            db,
            match_id,
            state=state,
            started_at=(
                datetime.now(timezone.utc) if state is GameState.ACTIVE else None
            ),
        )
        await db.commit()


async def _cancel(reset_db, match_id: str) -> None:
    async with reset_db() as db:
        match = (
            await db.execute(select(Match).where(Match.id == match_id))
        ).scalar_one()
        await cancel_match(db, match)


async def _state_in_db(reset_db, match_id: str) -> GameState:
    """Read the state through a SEPARATE session, so only committed work counts."""
    async with reset_db() as db:
        return (
            await db.execute(select(Match.state).where(Match.id == match_id))
        ).scalar_one()


async def test_commit_happens_before_the_task_is_stopped(
    reset_db, monkeypatch
) -> None:
    """The ordering contract, recorded as an event log.

    This is the test that fails against the bug: swap the two lines back in
    `cancel_match` and the log reads ["stop", "commit"].
    """
    await _seed(reset_db, "G_RACE", GameState.ACTIVE)

    events: list[str] = []
    monkeypatch.setattr(
        match_deletion.registry, "stop", lambda match_id: events.append("stop")
    )

    async with reset_db() as db:
        match = (
            await db.execute(select(Match).where(Match.id == "G_RACE"))
        ).scalar_one()
        real_commit = db.commit

        async def _spy_commit() -> None:
            await real_commit()
            events.append("commit")

        monkeypatch.setattr(db, "commit", _spy_commit)
        await cancel_match(db, match)

    assert events == ["commit", "stop"], (
        "cancel must commit CANCELLED before stopping the task, or the watchdog "
        f"can restart the match it just cancelled (got {events})"
    )
    assert await _state_in_db(reset_db, "G_RACE") == GameState.CANCELLED


async def test_the_watchdog_query_cannot_see_a_cancelled_match_afterwards(
    reset_db, monkeypatch
) -> None:
    """Run the watchdog's own query. It must not return the cancelled match.

    Asserting on the watchdog's exact query (ACTIVE with no running task) keeps
    this pinned to the real hazard rather than to cancel's internals.
    """
    await _seed(reset_db, "G_WD", GameState.ACTIVE)
    monkeypatch.setattr(match_deletion.registry, "stop", lambda match_id: None)

    await _cancel(reset_db, "G_WD")

    async with reset_db() as probe:
        restartable = (
            (
                await probe.execute(
                    select(Match.id).where(Match.state == GameState.ACTIVE)
                )
            )
            .scalars()
            .all()
        )
    assert "G_WD" not in list(restartable)


async def test_cancel_still_stops_the_task(reset_db, monkeypatch) -> None:
    """Reordering must not drop the stop — a cancelled match must not keep running."""
    await _seed(reset_db, "G_STOP", GameState.ACTIVE)

    stopped: list[str] = []
    monkeypatch.setattr(
        match_deletion.registry, "stop", lambda match_id: stopped.append(match_id)
    )

    await _cancel(reset_db, "G_STOP")

    assert stopped == ["G_STOP"]


@pytest.mark.parametrize("state", [GameState.SCHEDULED, GameState.REGISTERING])
async def test_cancel_still_works_for_pre_start_matches(
    reset_db, state, monkeypatch
) -> None:
    """A pre-start match has no running task; cancel must be unaffected."""
    monkeypatch.setattr(match_deletion.registry, "stop", lambda match_id: None)
    match_id = f"G_{state.value.upper()}"
    await _seed(reset_db, match_id, state)

    await _cancel(reset_db, match_id)

    assert await _state_in_db(reset_db, match_id) == GameState.CANCELLED
