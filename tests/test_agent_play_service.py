"""Direct tests for the shared agent-play service layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import agent_play
from app.engine.connection_activity import mark_seen
from app.engine.tokens import generate_turn_token
from app.games import get as get_game_module
from app.models import Connection, GameState, Match, Player, Turn
from app.models.game_state import MatchState, PlayerState
from app.models.turn import TurnSubmission
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
        # No far-future deadline is handed back — the agent is told to poll again
        # now (get_next_turn long-polls for it) instead of sleeping until a deadline.
        assert response.next_poll_after_seconds == 0

    async with reset_db() as db:
        stored = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        assert stored.turns_played == 1
        assert calls == [seed["player_agent_id"]]


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


async def test_next_turn_stamps_play_loop_heartbeat(reset_db, monkeypatch):
    """Calling get_next_turn records the play-loop heartbeat (last_polled_at) — the
    signal that an AI is actually running, which gates seating. A plain sign-in
    never reaches here, so it never sets this."""
    # The connection isn't marked live, so get_next_turn long-polls before
    # returning; shrink the hold so the test skips the full production wait. The
    # heartbeat assertion below is unchanged.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
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


async def _seed_liars_dice_turn(
    reset_db: async_sessionmaker,
    *,
    match_id: str,
    active_actor: str,
    user_i_base: int = 0,
) -> dict[str, object]:
    """An active Liar's Dice match mid-hand with an open act turn.

    Seats A (the caller's), B, C; `active_actor` says whose turn the table
    state records. `user_i_base` keeps user ids unique when one test seeds two
    matches in the same database. Returns the ids/tokens the submit path needs.
    """
    async with reset_db() as db:
        user = await make_user(db, user_i_base)
        connection, _key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        match = Match(
            id=match_id,
            name=f"match-{match_id}",
            game="liars-dice",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=30,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        players = [
            await seat_player(db, match.id, "A", user=user, connection=connection),
            await seat_player(db, match.id, "B", i=user_i_base + 1),
            await seat_player(db, match.id, "C", i=user_i_base + 2),
        ]
        db.add(
            MatchState(
                match_id=match.id,
                state_json={
                    "seat_order": ["A", "B", "C"],
                    "active_actor": active_actor,
                    "standing_bid": None,
                    "challenge_pending": False,
                },
            )
        )
        for player in players:
            db.add(
                PlayerState(
                    match_id=match.id,
                    player_id=player.id,
                    state_json={"dice": [1, 2], "dice_count": 2},
                )
            )
        turn = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=30),
            phase="act",
        )
        db.add(turn)
        await db.commit()
        return {
            "match_id": match.id,
            "player_id": players[0].id,
            "connection_id": connection.id,
            "turn_token": turn.turn_token,
            "agent_turn_token": f"{turn.turn_token}:{players[0].agent_id}:{match.id}",
        }


async def test_submit_action_strips_exactly_the_module_declared_snapshot_keys(
    reset_db, monkeypatch
):
    """The shared submit path merges the module's validation_snapshot into the
    move for validate_move (so NOT_YOUR_TURN etc. can fire), then strips exactly
    the keys the module declares in `validation_snapshot_keys` before
    record_submission — the vocabulary lives on the game module, not in shared
    code."""
    module = get_game_module("liars-dice")
    assert module.validation_snapshot_keys  # LD declares a real vocabulary

    captured: dict[str, dict[str, object]] = {}
    real_record_submission = module.record_submission

    async def spy(
        db: AsyncSession,
        turn: Turn,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
        is_connector_fallback: bool = False,
    ) -> None:
        captured["move"] = dict(move)
        await real_record_submission(
            db,
            turn,
            player,
            move,
            existing=existing,
            is_connector_fallback=is_connector_fallback,
        )

    monkeypatch.setattr(module, "record_submission", spy)

    # The snapshot IS merged for validation: with the action on seat B, seat A's
    # submit is rejected off the snapshot's active_actor before recording.
    seed = await _seed_liars_dice_turn(
        reset_db, match_id="M_LD_STRIP_B", active_actor="B"
    )
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.id == seed["player_id"]))
        ).scalar_one()
        connection = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        with pytest.raises(HTTPException) as exc:
            await agent_play.submit_action(
                db,
                match_id=seed["match_id"],
                player=player,
                connection=connection,
                agent_turn_token=seed["agent_turn_token"],
                turn_token=seed["turn_token"],
                action=None,
                target_id=None,
                message="",
                thinking="",
                is_connector_fallback=False,
                move={"type": "BID", "quantity": 1, "face": 2},
            )
        assert exc.value.detail["error"]["code"] == "NOT_YOUR_TURN"
        assert "move" not in captured  # nothing recorded on a rejected move

    # With seat A holding the action, the same submit lands — and the move that
    # reaches record_submission carries the caller's fields ONLY: every declared
    # snapshot key is stripped, nothing else is.
    seed = await _seed_liars_dice_turn(
        reset_db, match_id="M_LD_STRIP_A", active_actor="A", user_i_base=3
    )
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.id == seed["player_id"]))
        ).scalar_one()
        connection = (
            await db.execute(
                select(Connection).where(Connection.id == seed["connection_id"])
            )
        ).scalar_one()
        await agent_play.submit_action(
            db,
            match_id=seed["match_id"],
            player=player,
            connection=connection,
            agent_turn_token=seed["agent_turn_token"],
            turn_token=seed["turn_token"],
            action=None,
            target_id=None,
            message="going up",
            thinking="",
            is_connector_fallback=False,
            move={"type": "BID", "quantity": 1, "face": 2},
        )
    recorded = captured["move"]
    assert set(recorded) == {"type", "quantity", "face", "message", "thinking"}
    assert set(recorded).isdisjoint(module.validation_snapshot_keys)
    assert recorded["quantity"] == 1 and recorded["face"] == 2
