"""Game.game_type defaults to the PD module."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.models import Base, Game, GameState


@pytest.mark.asyncio
async def test_game_type_defaults_to_pd() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        g = Game(
            id="G_TT",
            name="t",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        assert g.game_type == "hoard-hurt-help"
    await engine.dispose()
