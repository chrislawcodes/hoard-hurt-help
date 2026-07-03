"""Regression test for the shared open-turn loader's sort tiebreak.

`load_open_turn` orders unresolved turns by round, then turn, then id, so a tie
on (round, turn) resolves to the most recently created row. The DB normally
forbids two turns sharing (match_id, round, turn), so this test drops that one
unique constraint while building its private schema to make the id tiebreak
observable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.agent_play_reads import load_open_turn
from app.models import Base
from app.models.match import Match, GameState
from app.models.turn import Turn


async def test_load_open_turn_breaks_round_turn_tie_by_id() -> None:
    # Build a private in-memory schema with the (match_id, round, turn)
    # uniqueness removed, so we can seed the tie the ordering must resolve.
    uq = next(
        c
        for c in Turn.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_turns_match_id_round_turn"
    )
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    Turn.__table__.constraints.discard(uq)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        Turn.__table__.constraints.add(uq)

    now = datetime.now(timezone.utc)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        match = Match(
            id="M_TIE",
            name="tie",
            game="hoard-hurt-help",
            state=GameState.ACTIVE,
            scheduled_start=now,
            started_at=now,
            per_turn_deadline_seconds=60,
        )
        db.add(match)
        await db.flush()

        for token in ("tk_first", "tk_second"):
            db.add(
                Turn(
                    match_id=match.id,
                    round=1,
                    turn=1,
                    turn_token=token,
                    opened_at=now,
                    deadline_at=now + timedelta(seconds=60),
                )
            )
            await db.flush()

        higher_id = (
            await db.execute(
                select(func.max(Turn.id)).where(Turn.match_id == match.id)
            )
        ).scalar_one()
        open_turn = await load_open_turn(db, match.id)

    assert open_turn is not None
    assert open_turn.id == higher_id
    assert open_turn.turn_token == "tk_second"

    await engine.dispose()
