"""The generic per-title state store persists, including in-place JSON edits.

`match_state` / `player_state` hold module-owned JSON for game #2+ (Liar's Dice).
The trap with a JSON column is that an in-place mutation (state["x"] = ...) does
not mark the column dirty, so it never persists. These tables use
MutableDict.as_mutable(JSON); this test proves an in-place edit survives a commit
and re-read. PD does not use these tables, so this is purely game-#2 plumbing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.models import Base, Match, GameState, MatchState, PlayerState
from tests.factories import seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_match_and_player_state_round_trip_with_inplace_edit() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        match = Match(
            id="M_STATE",
            name="state",
            game="liars-dice",
            state=GameState.ACTIVE,
            scheduled_start=_now(),
        )
        db.add(match)
        await db.flush()
        player = await seat_player(db, match.id, "P0", i=0)

        db.add(MatchState(match_id=match.id, state_json={"hand": 1}))
        db.add(PlayerState(match_id=match.id, player_id=player.id, state_json={"dice": [1, 2]}))
        await db.commit()

    # New session: mutate JSON in place (the dirty-tracking trap) and commit.
    async with factory() as db:
        ms = (await db.execute(select(MatchState).where(MatchState.match_id == "M_STATE"))).scalar_one()
        ms.state_json["hand"] = 2
        ms.state_json["standing_bid"] = {"quantity": 3, "face": 5}
        ps = (await db.execute(select(PlayerState).where(PlayerState.match_id == "M_STATE"))).scalar_one()
        ps.state_json["dice"] = [6, 6, 1]
        await db.commit()

    # Fresh session: the in-place edits must be there.
    async with factory() as db:
        ms = (await db.execute(select(MatchState).where(MatchState.match_id == "M_STATE"))).scalar_one()
        assert ms.state_json == {"hand": 2, "standing_bid": {"quantity": 3, "face": 5}}
        ps = (await db.execute(select(PlayerState).where(PlayerState.match_id == "M_STATE"))).scalar_one()
        assert ps.state_json == {"dice": [6, 6, 1]}

    await engine.dispose()
