"""Multi-turn grouping and ordering for the batched action-records reader.

`_load_public_action_records` fires two batched queries (one per turn-id set) and
groups in memory instead of two queries per turn. This pins the projected records
so the batched path stays byte-equivalent to the old per-turn path: turns emitted
oldest-to-newest, submissions within a turn in insertion (id) order, and a turn
message overriding its player's submission message.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.agent_play_reads import _load_public_action_records
from app.engine.tokens import generate_turn_token
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from tests.factories import seat_player


async def _resolved_turn(
    db: AsyncSession,
    match_id: str,
    rnd: int,
    turn: int,
    *,
    subs: Sequence[tuple[int, str, int | None, str, int]],
    messages: Iterable[tuple[int, str]] = (),
) -> Turn:
    """Seed one resolved turn. ``subs`` items are
    (player_id, action, target_player_id, message, points_delta)."""
    now = datetime.now(timezone.utc)
    t = Turn(
        match_id=match_id,
        round=rnd,
        turn=turn,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now,
        phase="act",
        resolved_at=now,
    )
    db.add(t)
    await db.flush()
    for player_id, text in messages:
        db.add(
            TurnMessage(
                turn_id=t.id,
                player_id=player_id,
                text=text,
                was_defaulted=False,
                submitted_at=now,
            )
        )
    for player_id, action, target, message, pts in subs:
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=player_id,
                action=action,
                target_player_id=target,
                message=message,
                points_delta=pts,
                round_score_after=0,
                was_defaulted=False,
                submitted_at=now,
            )
        )
    await db.flush()
    return t


async def test_batched_records_group_by_turn_in_order(db: AsyncSession) -> None:
    match = Match(
        id="G_BATCH",
        name="batch",
        game="hoard-hurt-help",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
    )
    db.add(match)
    await db.flush()
    p0 = await seat_player(db, match.id, "AI_0", i=0)
    p1 = await seat_player(db, match.id, "AI_1", i=1)
    p2 = await seat_player(db, match.id, "AI_2", i=2)
    await db.commit()

    # Seed the later turn first, so the reader must sort (round, turn) rather than
    # rely on insertion order.
    await _resolved_turn(
        db,
        match.id,
        1,
        2,
        subs=[
            (p0.id, "HELP", p1.id, "sub-a", 1),
            (p1.id, "HOARD", None, "sub-b", 2),
        ],
    )
    await _resolved_turn(
        db,
        match.id,
        1,
        1,
        subs=[
            (p0.id, "HOARD", None, "overridden", 0),
            (p1.id, "HURT", p0.id, "sub-c", -1),
            (p2.id, "HELP", p0.id, "sub-d", 1),
        ],
        messages=[(p0.id, "talk-from-p0")],
    )
    await db.commit()

    players = (
        (await db.execute(select(Player).where(Player.match_id == match.id)))
        .scalars()
        .all()
    )
    records = await _load_public_action_records(db, match.id, players)

    # Cross-turn: oldest to newest regardless of insertion order.
    assert [(r.round, r.turn) for r in records] == [
        (1, 1),
        (1, 1),
        (1, 1),
        (1, 2),
        (1, 2),
    ]

    turn11 = [r for r in records if (r.round, r.turn) == (1, 1)]
    # Within a turn: submissions in insertion (id) order.
    assert [r.actor_id for r in turn11] == ["AI_0", "AI_1", "AI_2"]
    # A turn message overrides that player's submission message; players with no
    # message row fall back to the submission's own message.
    assert turn11[0].message == "talk-from-p0"
    assert turn11[1].message == "sub-c"
    # Targets resolve player-id -> seat name.
    assert turn11[1].target_id == "AI_0"
    assert turn11[2].target_id == "AI_0"
    assert turn11[0].target_id is None

    turn12 = [r for r in records if (r.round, r.turn) == (1, 2)]
    assert [r.actor_id for r in turn12] == ["AI_0", "AI_1"]
    assert turn12[0].target_id == "AI_1"
    assert turn12[1].target_id is None

    # Windowing takes only the newest N resolved turns, still oldest-to-newest.
    windowed = await _load_public_action_records(db, match.id, players, recent_turns=1)
    assert [(r.round, r.turn) for r in windowed] == [(1, 2), (1, 2)]
