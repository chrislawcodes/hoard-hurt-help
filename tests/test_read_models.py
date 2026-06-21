"""Tests for shared read-side match projections."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import GameState, Match, Turn, TurnMessage, TurnSubmission
from app.models.agent import AgentKind
from app.models.player import Player
from app.read_models.matches import (
    count_players,
    count_players_by_match,
    load_action_records,
    load_match_timeline,
    load_player_records,
    load_scoreboard,
    winner_agent_id_by_player,
)
from app.routes.web_support import _agent_counts
from tests.factories import make_agent, make_user, seat_player


@contextmanager
def _count_selects(engine: AsyncEngine) -> Iterator[dict[str, int]]:
    """Count SELECT statements issued on the engine inside the block.

    Lets a test assert that a batch helper makes ONE grouped query instead of
    one query per match (the N+1 it was written to replace).
    """
    counter = {"n": 0}

    def _on_exec(conn, cursor, statement, params, context, executemany) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _on_exec)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_exec)


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


async def _seat_bot(db: AsyncSession, match_id: str, seat_name: str, i: int) -> Player:
    """Seat a built-in BOT player (kind=BOT) — excluded from real-agent counts."""
    user = await make_user(db, i)
    agent, _ = await make_agent(db, user, name=seat_name, kind=AgentKind.BOT)
    player = Player(
        match_id=match_id,
        user_id=user.id,
        agent_id=agent.id,
        seat_name=seat_name,
    )
    db.add(player)
    await db.flush()
    return player


@pytest.mark.asyncio
async def test_count_players_by_match_batches_matches_and_filters(
    db: AsyncSession, engine: AsyncEngine
) -> None:
    await _match(db, "M_001")
    await _match(db, "M_002")
    await _match(db, "M_003")  # a match with no players at all
    await seat_player(db, "M_001", "A1", i=1)
    left = await seat_player(db, "M_001", "A2", i=2)
    await seat_player(db, "M_002", "B1", i=3)
    left.left_at = datetime.now(timezone.utc)
    await db.commit()

    match_ids = ["M_001", "M_002", "M_003"]

    # Same numbers the per-match helper gives, for every match at once. A match
    # with no rows is simply absent — callers read a missing id as 0.
    all_counts = await count_players_by_match(db, match_ids)
    assert all_counts == {"M_001": 2, "M_002": 1}
    assert all_counts.get("M_003", 0) == 0

    active = await count_players_by_match(db, match_ids, active_only=True)
    assert active == {"M_001": 1, "M_002": 1}  # A2 left M_001

    assert await count_players_by_match(db, []) == {}

    # The whole point: one grouped query no matter how many matches are asked for.
    with _count_selects(engine) as counter:
        await count_players_by_match(db, match_ids)
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_winner_agent_id_by_player_batches(
    db: AsyncSession, engine: AsyncEngine
) -> None:
    await _match(db, "M_001")
    p1 = await seat_player(db, "M_001", "A1", i=1)
    p2 = await seat_player(db, "M_001", "A2", i=2)
    await db.commit()

    mapping = await winner_agent_id_by_player(db, [p1.id, p2.id])
    assert mapping == {p1.id: p1.agent_id, p2.id: p2.agent_id}
    assert await winner_agent_id_by_player(db, []) == {}

    with _count_selects(engine) as counter:
        await winner_agent_id_by_player(db, [p1.id, p2.id])
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_agent_counts_excludes_bots_and_batches(
    db: AsyncSession, engine: AsyncEngine
) -> None:
    await _match(db, "M_001")
    await _match(db, "M_002")
    await _match(db, "M_003")  # bots only — should be absent (count 0)
    await seat_player(db, "M_001", "A1", i=1)  # real agent (kind=AI)
    await seat_player(db, "M_001", "A2", i=2)  # real agent
    await _seat_bot(db, "M_001", "Bot1", i=3)  # bot — not counted
    await seat_player(db, "M_002", "B1", i=4)  # real agent
    await _seat_bot(db, "M_003", "Bot2", i=5)  # bot — match has no real agents
    await db.commit()

    match_ids = ["M_001", "M_002", "M_003"]
    counts = await _agent_counts(db, match_ids)
    assert counts == {"M_001": 2, "M_002": 1}  # bots excluded, M_003 absent
    assert await _agent_counts(db, []) == {}

    with _count_selects(engine) as counter:
        await _agent_counts(db, match_ids)
    assert counter["n"] == 1
