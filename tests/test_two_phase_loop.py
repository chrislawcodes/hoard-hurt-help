"""Two-phase turn loop tests: talk defaulting, quorum, and resume tri-state."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine import scheduler
from app.engine.resolver import finalize_talk_phase
from app.engine.scheduler_turn_loop import _all_messaged
from app.models import Match, Player, Turn, TurnMessage
from tests.factories import make_match_with_seated_players


# Autouse delegate to tests/conftest.py's quiet_scheduler: this file's turn-loop
# tests need all three of quiet_scheduler's stubs (SessionLocal pointed at the
# test database, since scheduler imported SessionLocal by name and the app.db
# string-path patch alone doesn't reach it; the talk/act waits returned
# immediately so a turn resolves without a real deadline).
@pytest.fixture(autouse=True)
async def _quiet_scheduler(quiet_scheduler: None) -> None:
    return quiet_scheduler


@pytest.fixture
async def db(reset_db: async_sessionmaker) -> AsyncIterator[AsyncSession]:
    async with reset_db() as session:
        yield session


@pytest.fixture
def published(monkeypatch):
    events: list[tuple[str, str, dict]] = []

    async def fake_publish(channel: str, event_type: str, payload: dict) -> None:
        events.append((channel, event_type, payload))

    monkeypatch.setattr(scheduler, "publish", fake_publish)
    return events


async def _make_two_phase_game_with_agents(db: AsyncSession, n: int) -> tuple[Match, list[Player]]:
    return await make_match_with_seated_players(db, "G_TEST", n, total_rounds=1, turns_per_round=1)


async def _open_turn(
    db: AsyncSession,
    game: Match,
    round_num: int = 1,
    turn_num: int = 1,
    *,
    phase: str = "talk",
) -> Turn:
    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id=game.id,
        round=round_num,
        turn=turn_num,
        turn_token=f"tk_{round_num}_{turn_num}",
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
        phase=phase,
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)
    return turn


async def _add_message(
    db: AsyncSession,
    turn: Turn,
    player: Player,
    *,
    text: str = "hello",
    thinking: str = "why",
    was_defaulted: bool = False,
) -> TurnMessage:
    msg = TurnMessage(
        turn_id=turn.id,
        player_id=player.id,
        text=text,
        thinking=thinking,
        was_defaulted=was_defaulted,
        submitted_at=None if was_defaulted else datetime.now(timezone.utc),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def _load_messages(db: AsyncSession, turn_id: int) -> list[TurnMessage]:
    return list(
        (
            await db.execute(select(TurnMessage).where(TurnMessage.turn_id == turn_id))
        )
        .scalars()
        .all()
    )


async def test_finalize_talk_phase_defaulted_rows_skip_left_players(db):
    game, players = await _make_two_phase_game_with_agents(db, 3)
    a, b, c = players
    c.left_at = datetime.now(timezone.utc)
    await db.commit()

    turn = await _open_turn(db, game)
    await _add_message(db, turn, a, text="hi a")

    await finalize_talk_phase(db, turn)

    fresh_turn = (
        await db.execute(select(Turn).where(Turn.id == turn.id))
    ).scalar_one()
    assert fresh_turn.talk_resolved_at is not None

    messages = await _load_messages(db, turn.id)
    assert {m.player_id for m in messages} == {a.id, b.id}
    defaulted = {m.player_id for m in messages if m.was_defaulted}
    assert defaulted == {b.id}
    by_player = {m.player_id: m for m in messages}
    assert by_player[a.id].text == "hi a"
    assert by_player[a.id].was_defaulted is False
    assert by_player[b.id].text == ""
    assert by_player[b.id].thinking == ""
    assert by_player[b.id].submitted_at is None


async def test_all_messaged_requires_non_defaulted_messages_for_active_players(db):
    game, players = await _make_two_phase_game_with_agents(db, 3)
    a, b, c = players
    c.left_at = datetime.now(timezone.utc)
    await db.commit()

    turn = await _open_turn(db, game)
    await _add_message(db, turn, a)
    await _add_message(db, turn, b, was_defaulted=True)

    assert await _all_messaged(db, turn) is False

    msg_b = (
        await db.execute(
            select(TurnMessage).where(
                TurnMessage.turn_id == turn.id, TurnMessage.player_id == b.id
            )
        )
    ).scalar_one()
    msg_b.was_defaulted = False
    msg_b.submitted_at = datetime.now(timezone.utc)
    await db.commit()

    assert await _all_messaged(db, turn) is True


async def test_two_phase_loop_fresh_turn_defaults_talk_then_resolves_act(
    db, published
):
    game, players = await _make_two_phase_game_with_agents(db, 2)
    a, b = players

    turn = await _open_turn(db, game)
    await _add_message(db, turn, a, text="talk")

    await asyncio.wait_for(scheduler._run_game(game.id), timeout=5)

    async with scheduler.SessionLocal() as fresh_db:
        fresh_turn = (
            await fresh_db.execute(select(Turn).where(Turn.id == turn.id))
        ).scalar_one()
        messages = await _load_messages(fresh_db, turn.id)

    assert fresh_turn.talk_resolved_at is not None
    assert fresh_turn.resolved_at is not None
    assert fresh_turn.phase == "act"
    assert len(messages) == 2
    assert {m.player_id for m in messages if m.was_defaulted} == {b.id}
    assert [event for _, event, _ in published[:3]] == [
        "turn_opened",
        "turn_talked",
        "turn_opened",
    ]
    assert published[0][2]["phase"] == "talk"
    assert published[2][2]["phase"] == "act"


async def test_two_phase_loop_resume_after_talk_resolution_skips_defaulting(
    db, published
):
    game, players = await _make_two_phase_game_with_agents(db, 2)
    a, _b = players

    turn = await _open_turn(db, game, phase="talk")
    await _add_message(db, turn, a, text="talk")
    turn.talk_resolved_at = datetime.now(timezone.utc)
    await db.commit()

    await asyncio.wait_for(scheduler._run_game(game.id), timeout=5)

    async with scheduler.SessionLocal() as fresh_db:
        fresh_turn = (
            await fresh_db.execute(select(Turn).where(Turn.id == turn.id))
        ).scalar_one()
        messages = await _load_messages(fresh_db, turn.id)

    assert fresh_turn.resolved_at is not None
    assert fresh_turn.phase == "act"
    assert len(messages) == 1
    assert messages[0].player_id == a.id
    assert messages[0].was_defaulted is False
    assert all(event != "turn_talked" for _, event, _ in published)
    assert published[0][1] == "turn_opened"
    assert published[0][2]["phase"] == "act"


async def test_two_phase_loop_skips_already_resolved_turns(db, published):
    game, players = await _make_two_phase_game_with_agents(db, 2)
    a, _ = players

    turn = await _open_turn(db, game, phase="act")
    await _add_message(db, turn, a, text="talk")
    turn.talk_resolved_at = datetime.now(timezone.utc)
    turn.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    await asyncio.wait_for(scheduler._run_game(game.id), timeout=5)

    async with scheduler.SessionLocal() as fresh_db:
        fresh_turn = (
            await fresh_db.execute(select(Turn).where(Turn.id == turn.id))
        ).scalar_one()
        messages = await _load_messages(fresh_db, turn.id)

    assert fresh_turn.resolved_at is not None
    assert len(messages) == 1
    assert all(event not in {"turn_opened", "turn_talked", "turn_resolved"} for _, event, _ in published)
