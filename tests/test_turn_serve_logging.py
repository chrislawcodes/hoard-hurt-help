"""Turn serving leaves a record of who got what.

Nothing else does. ``Player.served_by_connection_id`` is per-seat, not per-turn,
so it cannot answer "was this turn served twice?" — and that question is the one
that matters for cost. Two client sessions for one agent both pass the claim in
``_claim_pin`` (it only excludes OTHER connections, and sessions from one client
share a connection), so both are served the same turn and both pay for a full
model think on it.

That happened in a real run and could not be confirmed afterwards, because the
only evidence was client-side logs. These tests pin the server-side record.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.engine.agent_play_next_turn import get_next_turn
from app.models.match import GameState
from tests.factories import (
    make_agent,
    make_connection,
    make_match,
    make_turn,
    make_user,
    seat_prebuilt_player,
)


def _events(caplog, name: str) -> list[str]:
    """Ops-event lines of one kind. The prefix is the documented grep contract."""
    return [r.getMessage() for r in caplog.records if f"ops_event={name}" in r.getMessage()]


async def _seed_open_turn(db, *, match_id: str = "M_LOG"):
    """A user with one agent seated in an ACTIVE match with an open act turn."""
    user = await make_user(db)
    connection, _key = await make_connection(
        db, user, last_seen_at=datetime.now(timezone.utc)
    )
    agent, version = await make_agent(db, user, connection=connection, name="Logged")
    assert version is not None
    match = await make_match(
        db,
        match_id,
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc) - timedelta(minutes=1),
        started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    # chosen_provider stays None: routing treats that as "any live connection may
    # serve it", which keeps this test about the logging rather than about
    # provider routing.
    await seat_prebuilt_player(
        db, match=match, user=user, agent=agent, version=version, seat_name="Logged"
    )
    await make_turn(db, match.id, phase="act", resolved=False)
    await db.commit()
    return connection, agent, match


async def test_serving_a_turn_logs_who_got_what(reset_db, caplog) -> None:
    async with reset_db() as db:
        connection, agent, match = await _seed_open_turn(db)

        with caplog.at_level(logging.INFO):
            payload = await get_next_turn(db, connection, max_hold_seconds=0)

    assert payload["status"] == "your_turn"
    served = _events(caplog, "turn_served")
    assert len(served) == 1, served
    line = served[0]
    # Every field needed to spot a double-serve must be present.
    assert f"agent_id={agent.id}" in line
    assert f"match_id={match.id}" in line
    assert f"connection_id={connection.id}" in line
    assert "round=" in line
    assert "turn=" in line


async def test_the_same_turn_served_twice_is_visible(reset_db, caplog) -> None:
    """The whole point: a double-serve must be findable in the log.

    Two polls on one connection for one agent — the exact shape of a user who
    pasted the play prompt into two client sessions. Both are served, and the
    two lines must agree on agent + match + round + turn so a grep can pair
    them.
    """
    async with reset_db() as db:
        connection, agent, match = await _seed_open_turn(db, match_id="M_DUP")

        with caplog.at_level(logging.INFO):
            first = await get_next_turn(db, connection, max_hold_seconds=0)
            second = await get_next_turn(db, connection, max_hold_seconds=0)

    # Both sessions really are served — nothing on the server stops this today.
    assert first["status"] == "your_turn"
    assert second["status"] == "your_turn"

    served = _events(caplog, "turn_served")
    assert len(served) == 2, served

    def _key(line: str) -> tuple[str, ...]:
        fields = dict(
            part.split("=", 1)
            for part in line.split(" | ")[0].split()
            if "=" in part
        )
        return (
            fields["agent_id"],
            fields["match_id"],
            fields["round"],
            fields["turn"],
        )

    assert _key(served[0]) == _key(served[1]), (
        "a double-serve must produce two lines sharing agent/match/round/turn, "
        "or it cannot be found by grep"
    )


async def test_an_idle_poll_logs_the_pacing_it_handed_back(reset_db, caplog) -> None:
    """The other half: what did the server tell a client that got no turn?"""
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(
            db, user, last_seen_at=datetime.now(timezone.utc)
        )
        await make_agent(db, user, connection=connection, name="Idle")
        await db.commit()

        with caplog.at_level(logging.DEBUG):
            payload = await get_next_turn(db, connection, max_hold_seconds=0)

    assert payload["status"] in ("waiting", "no_game")
    idle = _events(caplog, "turn_poll_idle")
    assert len(idle) == 1, idle
    line = idle[0]
    # next_poll_after_seconds is the number a client with no timer must somehow
    # honour, so it is the field worth having in the log.
    assert "next_poll_after_seconds=" in line
    assert "should_stop=" in line
    assert "has_game=" in line


async def test_idle_polls_stay_off_at_info(reset_db, caplog) -> None:
    """Idle logging is DEBUG on purpose — it fires on every poll.

    If it leaked to INFO it would drown the turn_served lines that matter.
    """
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(
            db, user, last_seen_at=datetime.now(timezone.utc)
        )
        await make_agent(db, user, connection=connection, name="Quiet")
        await db.commit()

        with caplog.at_level(logging.INFO):
            await get_next_turn(db, connection, max_hold_seconds=0)

    assert _events(caplog, "turn_poll_idle") == []
