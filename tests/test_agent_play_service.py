"""Direct tests for the shared agent-play service layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine import agent_play
from app.engine.connection_activity import mark_seen
from app.engine.tokens import generate_turn_token
from app.models import Connection, GameState, Match, Player, Turn
from tests.factories import make_connection, make_user, seat_player


async def _seed_turn(
    reset_db: async_sessionmaker,
    *,
    match_id: str,
) -> dict[str, object]:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        match = Match(
            id=match_id,
            name=f"match-{match_id}",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        player = await seat_player(
            db,
            match.id,
            "AI_0",
            user=user,
            connection=connection,
        )
        turn = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase="act",
        )
        db.add(turn)
        await db.commit()
        return {
            "match_id": match.id,
            "player_id": player.id,
            "player_agent_id": player.agent_id,
            "connection_id": connection.id,
            "turn_token": turn.turn_token,
            "agent_turn_token": f"{turn.turn_token}:{player.agent_id}:{match.id}",
        }


@pytest.mark.asyncio
async def test_poll_turn_service_rate_limits(reset_db):
    seed = await _seed_turn(reset_db, match_id="M_SERVICE_1")

    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.id == seed["player_id"]))
        ).scalar_one()
        match = (
            await db.execute(select(Match).where(Match.id == seed["match_id"]))
        ).scalar_one()
        rate_state: dict[int, float] = {}

        first = await agent_play.poll_turn(
            db,
            match_id=match.id,
            player=player,
            rate_state=rate_state,
        )
        assert first.status == "your_turn"

        with pytest.raises(HTTPException) as exc:
            await agent_play.poll_turn(
                db,
                match_id=match.id,
                player=player,
                rate_state=rate_state,
            )
        assert exc.value.status_code == 429
        assert exc.value.detail["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_submit_action_service_updates_turn_count_and_first_move(
    reset_db, monkeypatch
):
    seed = await _seed_turn(reset_db, match_id="M_SERVICE_2")

    calls: list[int] = []

    async def fake_mark_first_move(db, bot_id: int) -> None:  # noqa: ANN001
        calls.append(bot_id)

    monkeypatch.setattr(agent_play, "mark_first_move", fake_mark_first_move)

    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.id == seed["player_id"]))
        ).scalar_one()
        connection = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()

        response = await agent_play.submit_action(
            db,
            match_id=seed["match_id"],
            player=player,
            connection=connection,
            agent_turn_token=seed["agent_turn_token"],
            turn_token=seed["turn_token"],
            action="HOARD",
            target_id=None,
            message="mine",
            thinking="",
            is_connector_fallback=False,
        )
        assert response.turn_will_resolve_at is not None

    async with reset_db() as db:
        stored = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        assert stored.turns_played == 1
        assert calls == [seed["player_agent_id"]]


@pytest.mark.asyncio
async def test_next_turn_service_returns_payload(reset_db):
    seed = await _seed_turn(reset_db, match_id="M_SERVICE_3")

    async with reset_db() as db:
        connection = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        await mark_seen(db, connection, key_hash=connection.key_lookup)
        response = await agent_play.get_next_turn(db, connection)
        assert response["status"] == "your_turn"
        assert response["match_id"] == seed["match_id"]
        assert response["turn_token"] == seed["turn_token"]


@pytest.mark.asyncio
async def test_next_turn_stamps_play_loop_heartbeat(reset_db):
    """Calling get_next_turn records the play-loop heartbeat (last_polled_at) — the
    signal that an AI is actually running, which gates seating. A plain sign-in
    never reaches here, so it never sets this."""
    seed = await _seed_turn(reset_db, match_id="M_SERVICE_HB")
    async with reset_db() as db:
        connection = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        assert connection.last_polled_at is None
        await agent_play.get_next_turn(db, connection)
    async with reset_db() as db:
        refreshed = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        assert refreshed.last_polled_at is not None


@pytest.mark.asyncio
async def test_next_turns_stamps_play_loop_heartbeat_when_waiting(reset_db):
    """get_next_turns (the fan-out discovery call) is the AI running its play loop
    too, so it must stamp the heartbeat (last_polled_at) EVEN when no turn is due.

    Regression: a freshly connected agent waiting for its first match to start
    polls only get_next_turns and gets "waiting" back. If that path doesn't stamp
    last_polled_at, provider_readiness never reaches LIVE, the held seat never
    auto-confirms, and the connect page waits forever.
    """
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        assert connection.last_polled_at is None
        # No match or turn seeded → the AI is waiting, not serving a turn.
        response = await agent_play.get_next_turns(db, connection)
        assert response["status"] != "your_turn"
        connection_id = connection.id
    async with reset_db() as db:
        refreshed = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        assert refreshed.last_polled_at is not None
