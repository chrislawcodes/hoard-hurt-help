"""The BaseGameModule defaults reproduce PD's behavior.

These hooks (added so a sequential/hidden game can override them) must, by
default, behave exactly as PD always has: no private state, no extra public
state, HOARD as the missed-turn move, fixed-grid match end, and the
round-wins-then-score finish order. PD inherits these unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.games.hoard_hurt_help.game import HoardHurtHelp
from app.models import Base, Match, GameState
from tests.factories import seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_pd_inherits_default_hooks() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = HoardHurtHelp()

    async with factory() as db:
        match = Match(
            id="M_DEF",
            name="def",
            game="hoard-hurt-help",
            state=GameState.ACTIVE,
            scheduled_start=_now(),
            total_rounds=10,
            rounds_awarded=3,
        )
        db.add(match)
        await db.flush()
        p1 = await seat_player(db, match.id, "A", i=0)
        p2 = await seat_player(db, match.id, "B", i=1)
        p1.total_round_wins, p1.total_round_score = 2.0, 30
        p2.total_round_wins, p2.total_round_score = 2.0, 45  # ties wins, higher score
        await db.commit()

        assert await module.default_move(db, match, p1) == {"action": "HOARD", "target_id": None}
        assert await module.private_state_for(db, match, p1) == {}
        assert await module.public_state_for(db, match, p1) == {}

        assert await module.is_match_over(db, match) is False  # 3 < 10
        match.rounds_awarded = 10
        assert await module.is_match_over(db, match) is True

        # Finish order: equal wins → higher total score first.
        assert await module.final_placement(db, match) == [p2.id, p1.id]

    await engine.dispose()
