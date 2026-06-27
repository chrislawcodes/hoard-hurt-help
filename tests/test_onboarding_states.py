"""C5 dedup: the shared `has_moved` onboarding primitive.

Pins the boundary both former `_has_moved` copies (connection_activity +
agent_onboarding) relied on: an agent has "moved" iff it has at least one
NON-defaulted TurnSubmission. A defaulted-only agent has not moved.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine.onboarding_states import PREGAME_STATES, has_moved
from app.models import GameState, Match, Player
from app.models.turn import Turn, TurnSubmission
from tests.factories import make_agent, make_user


async def _seed_turn(db) -> tuple[int, int]:
    """Return (agent_id, turn_id) for a player seated in a fresh match."""
    user = await make_user(db, 0)
    agent, _ = await make_agent(db, user, name="mover")
    db.add(
        Match(
            id="G_C5",
            name="C5",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            per_turn_deadline_seconds=60,
        )
    )
    player = Player(match_id="G_C5", user_id=user.id, agent_id=agent.id, seat_name="mover")
    db.add(player)
    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id="G_C5",
        round=1,
        turn=1,
        turn_token="tok-c5-1",
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
        phase="act",
    )
    db.add(turn)
    await db.flush()
    return agent.id, turn.id


async def _add_submission(db, turn_id: int, agent_id: int, *, defaulted: bool) -> None:
    player_id = (
        await db.execute(_player_id_select(agent_id))
    ).scalar_one()
    db.add(
        TurnSubmission(
            turn_id=turn_id,
            player_id=player_id,
            action="HOARD",
            was_defaulted=defaulted,
        )
    )
    await db.flush()


def _player_id_select(agent_id: int):
    from sqlalchemy import select

    return select(Player.id).where(Player.agent_id == agent_id)


async def test_has_not_moved_with_defaulted_only(db) -> None:
    agent_id, turn_id = await _seed_turn(db)
    await _add_submission(db, turn_id, agent_id, defaulted=True)
    assert await has_moved(db, agent_id) is False


async def test_has_moved_with_one_real_submission(db) -> None:
    agent_id, turn_id = await _seed_turn(db)
    await _add_submission(db, turn_id, agent_id, defaulted=False)
    assert await has_moved(db, agent_id) is True


def test_pregame_states_value() -> None:
    assert PREGAME_STATES == (GameState.SCHEDULED, GameState.REGISTERING)
