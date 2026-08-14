"""A sequential game's open turn is served to one seat: whoever is on the clock.

Turn serving decides who owes a move by asking "has this seat submitted yet?".
That is right for a simultaneous game (PD), where every seat owes a move every
turn. It is wrong for a sequential one (Liar's Dice), where the module names a
single active actor and ``validate_move`` answers every other seat with
NOT_YOUR_TURN.

Two things go wrong without the gate:

* A seat that cannot act is handed a full turn payload and pays for a model
  think, then gets a 400 back. Nothing about that marks the seat as done, so the
  next poll serves it again — a paid loop with no exit.
* A user holding two seats in one match is worse off than that. All else equal,
  ``select_next_turn`` breaks the tie by agent id, so when the idle seat sorts
  first the seat that actually owes the move is never served at all and its turn
  runs out the clock and defaults.

The gate must not reach a simultaneous game: ``BaseGameModule.next_actor`` is
deliberately fail-loud, so consulting it on the PD path would turn every PD poll
into a 500. The last test pins that boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.engine.agent_play_next_turn import get_next_turn, get_next_turns
from app.engine.connection_auth_loading import connection_user_load_options
from app.games.liars_dice.game import LiarsDice
from app.models.connection import Connection
from app.models.game_state import MatchState, PlayerState
from app.models.match import GameState
from tests.factories import (
    make_agent,
    make_connection,
    make_match,
    make_turn,
    make_user,
    seat_prebuilt_player,
)

_SEATS = ("A", "B", "C")


async def _poll(session_factory, connection_id: int) -> dict:
    """One next-turn poll in its own session, the way an HTTP request arrives.

    Sessions matter here: a poll with nothing to serve rolls back, which expires
    every object in its session. Sharing one session across several polls would
    make the second one fail on a stale attribute for reasons that have nothing
    to do with the gate.
    """
    async with session_factory() as db:
        connection = (
            await db.execute(
                select(Connection)
                .options(connection_user_load_options())
                .where(Connection.id == connection_id)
            )
        ).scalar_one()
        return await get_next_turn(db, connection, max_hold_seconds=0)


async def _seed_liars_dice_match(db, *, match_id: str, active_actor: str, seats=_SEATS):
    """An ACTIVE Liar's Dice match with an open act turn and a named active actor.

    Every seat gets its own user + connection, so each one can poll as itself.
    Returns {seat_name: connection}.
    """
    now = datetime.now(timezone.utc)
    match = await make_match(
        db,
        match_id,
        state=GameState.ACTIVE,
        scheduled_start=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
    )
    match.game = LiarsDice.game_type

    connections = {}
    for index, seat_name in enumerate(seats):
        user = await make_user(db, index)
        connection, _key = await make_connection(db, user, last_seen_at=now)
        agent, version = await make_agent(
            db, user, connection=connection, name=seat_name
        )
        assert version is not None
        player = await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name=seat_name
        )
        db.add(
            PlayerState(
                match_id=match.id,
                player_id=player.id,
                state_json={"dice": [1, 2, 3], "dice_count": 3},
            )
        )
        connections[seat_name] = connection

    db.add(
        MatchState(
            match_id=match.id,
            state_json={
                "config": {"wild_ones": True, "dice_per_player": 3},
                "seat_order": list(seats),
                "active_actor": active_actor,
                "standing_bid": None,
                "elimination_order": [],
            },
        )
    )
    await make_turn(db, match.id, phase="act", resolved=False)
    await db.commit()
    return match, connections


async def test_only_the_active_actor_is_served_the_open_turn(reset_db) -> None:
    """The seat on the clock gets the turn; the other two are told to wait."""
    async with reset_db() as db:
        _match, connections = await _seed_liars_dice_match(
            db, match_id="M_LD_GATE", active_actor="B"
        )
        connection_ids = {seat: conn.id for seat, conn in connections.items()}

    served = {
        seat: await _poll(reset_db, connection_id)
        for seat, connection_id in connection_ids.items()
    }

    assert served["B"]["status"] == "your_turn"
    assert served["B"]["seat_name"] == "B"
    # A and C cannot legally submit, so handing them a turn only buys a 400.
    assert served["A"]["status"] == "waiting", served["A"]
    assert served["C"]["status"] == "waiting", served["C"]


async def test_an_idle_seat_does_not_starve_the_seat_that_owes_the_move(
    reset_db,
) -> None:
    """One connection, two seats in one match: the active one is served.

    The tie-break in ``select_next_turn`` is by agent id, and the idle seat is
    created first here, so it holds the lower id. Ungated, it wins the tie every
    poll and the active seat never gets its turn.
    """
    now = datetime.now(timezone.utc)
    async with reset_db() as db:
        match = await make_match(
            db,
            "M_LD_STARVE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
        )
        match.game = LiarsDice.game_type

        user = await make_user(db)
        connection, _key = await make_connection(db, user, last_seen_at=now)
        # "Idle" is seated first, so it holds the lower agent id and wins the tie.
        for seat_name in ("Idle", "OnTheClock"):
            agent, version = await make_agent(
                db, user, connection=connection, name=seat_name
            )
            assert version is not None
            player = await seat_prebuilt_player(
                db,
                match=match,
                user=user,
                agent=agent,
                version=version,
                seat_name=seat_name,
            )
            db.add(
                PlayerState(
                    match_id=match.id,
                    player_id=player.id,
                    state_json={"dice": [1, 2, 3], "dice_count": 3},
                )
            )
        db.add(
            MatchState(
                match_id=match.id,
                state_json={
                    "config": {"wild_ones": True, "dice_per_player": 3},
                    "seat_order": ["Idle", "OnTheClock"],
                    "active_actor": "OnTheClock",
                    "standing_bid": None,
                    "elimination_order": [],
                },
            )
        )
        await make_turn(db, match.id, phase="act", resolved=False)
        await db.commit()

        one = await get_next_turn(db, connection, max_hold_seconds=0)
        fanned_out = await get_next_turns(db, connection)

    assert one["status"] == "your_turn"
    assert one["seat_name"] == "OnTheClock"
    # The fan-out endpoint claims every eligible turn at once, so it has to apply
    # the same gate or it hands back both seats.
    assert fanned_out["status"] == "your_turn"
    assert [turn["seat_name"] for turn in fanned_out["turns"]] == ["OnTheClock"]


async def test_a_simultaneous_game_never_consults_next_actor(
    reset_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PD serves every seat, and must not touch the fail-loud sequential hook."""
    from app.games.hoard_hurt_help.game import HoardHurtHelp

    async def _explode(*_args, **_kwargs):
        raise AssertionError("next_actor must not be consulted for a PD poll")

    monkeypatch.setattr(HoardHurtHelp, "next_actor", _explode)

    now = datetime.now(timezone.utc)
    async with reset_db() as db:
        match = await make_match(
            db,
            "M_PD_GATE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
        )
        user = await make_user(db)
        connection, _key = await make_connection(db, user, last_seen_at=now)
        agent, version = await make_agent(db, user, connection=connection, name="Pd")
        assert version is not None
        await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="Pd"
        )
        await make_turn(db, match.id, phase="act", resolved=False)
        await db.commit()

        payload = await get_next_turn(db, connection, max_hold_seconds=0)

    assert payload["status"] == "your_turn"
    assert payload["seat_name"] == "Pd"
