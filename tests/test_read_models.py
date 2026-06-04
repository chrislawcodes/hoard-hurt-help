"""Tests for shared read-side match projections."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Base, GameState, Match, Turn, TurnMessage, TurnSubmission
from app.read_models.matches import (
    count_players,
    load_action_records,
    load_match_timeline,
    load_player_records,
    load_scoreboard,
)
from tests.factories import seat_player


@pytest.fixture
async def db(engine, session_factory: async_sessionmaker) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session


async def _match(db: AsyncSession, match_id: str = "M_001") -> Match:
    match = Match(
        id=match_id,
        name="Read Model Test",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
    )
    db.add(match)
    await db.flush()
    return match


@pytest.mark.asyncio
async def test_load_action_records_prefers_talk_message_over_submission_message(
    db: AsyncSession,
) -> None:
    match = await _match(db)
    alice = await seat_player(db, match.id, "Alice", i=1)
    bob = await seat_player(db, match.id, "Bob", i=2)
    turn = Turn(
        match_id=match.id,
        round=1,
        turn=1,
        turn_token="tk",
        opened_at=datetime.now(timezone.utc),
        deadline_at=datetime.now(timezone.utc),
        phase="act",
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(turn)
    await db.flush()
    db.add(
        TurnMessage(
            turn_id=turn.id,
            player_id=alice.id,
            text="talk phase message",
            thinking="",
            was_defaulted=False,
            submitted_at=datetime.now(timezone.utc),
        )
    )
    db.add_all(
        [
            TurnSubmission(
                turn_id=turn.id,
                player_id=alice.id,
                action="HELP",
                target_player_id=bob.id,
                message="legacy alice message",
                points_delta=0,
                round_score_after=0,
            ),
            TurnSubmission(
                turn_id=turn.id,
                player_id=bob.id,
                action="HOARD",
                target_player_id=None,
                message="legacy bob message",
                points_delta=2,
                round_score_after=2,
            ),
        ]
    )
    await db.commit()

    actions = await load_action_records(db, match.id)

    assert [(a.actor_id, a.action, a.target_id) for a in actions] == [
        ("Alice", "HELP", "Bob"),
        ("Bob", "HOARD", None),
    ]
    assert actions[0].message == "talk phase message"
    assert actions[1].message == "legacy bob message"


@pytest.mark.asyncio
async def test_load_match_timeline_resolves_agents_and_falls_back_to_submission_messages(
    db: AsyncSession,
) -> None:
    match = await _match(db)
    alice = await seat_player(db, match.id, "Alice", i=1)
    bob = await seat_player(db, match.id, "Bob", i=2)
    turn = Turn(
        match_id=match.id,
        round=1,
        turn=1,
        turn_token="tk",
        opened_at=datetime.now(timezone.utc),
        deadline_at=datetime.now(timezone.utc),
        phase="act",
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(turn)
    await db.flush()
    submitted_at = datetime.now(timezone.utc)
    db.add_all(
        [
            TurnSubmission(
                turn_id=turn.id,
                player_id=alice.id,
                action="HELP",
                target_player_id=bob.id,
                message="fallback public message",
                thinking="private act thinking",
                points_delta=0,
                round_score_after=0,
                was_defaulted=False,
                submitted_at=submitted_at,
            ),
            TurnSubmission(
                turn_id=turn.id,
                player_id=bob.id,
                action="HOARD",
                target_player_id=None,
                message="bob banks",
                points_delta=2,
                round_score_after=2,
                submitted_at=submitted_at,
            ),
        ]
    )
    await db.commit()

    timeline = await load_match_timeline(db, match.id)

    assert len(timeline) == 1
    assert [(m.agent_id, m.text, m.thinking) for m in timeline[0].messages] == [
        ("Alice", "fallback public message", ""),
        ("Bob", "bob banks", ""),
    ]
    assert [
        (a.agent_id, a.action, a.target_id, a.message, a.thinking)
        for a in timeline[0].actions
    ] == [
        ("Alice", "HELP", "Bob", "fallback public message", "private act thinking"),
        ("Bob", "HOARD", None, "bob banks", ""),
    ]


@pytest.mark.asyncio
async def test_player_read_models_make_active_filter_explicit(db: AsyncSession) -> None:
    match = await _match(db)
    active = await seat_player(db, match.id, "Active", i=1)
    left = await seat_player(db, match.id, "Left", i=2)
    active.current_round_score = 7
    active.total_round_score = 17
    active.total_round_wins = 1.0
    left.current_round_score = 3
    left.left_at = datetime.now(timezone.utc)
    await db.commit()

    assert await count_players(db, match.id) == 2
    assert await count_players(db, match.id, active_only=True) == 1

    active_records = await load_player_records(db, match.id)
    all_records = await load_player_records(db, match.id, active_only=False)
    active_scoreboard = await load_scoreboard(db, match.id, active_only=True)

    assert [p.agent_id for p in active_records] == ["Active"]
    assert [p.agent_id for p in all_records] == ["Active", "Left"]
    assert [(row.agent_id, row.round_score) for row in active_scoreboard] == [
        ("Active", 7)
    ]
