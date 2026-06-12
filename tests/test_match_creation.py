"""Unit tests for shared match creation behavior."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.match_creation import create_match
from app.models import Base
from app.models.match import GameState, Match


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_match_retries_after_pk_collision(session, monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    session.add(
        Match(
            id="M_0001",
            name="Existing",
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
        )
    )
    await session.commit()

    ids = ["M_0001", "M_0002"]

    async def _allocate(_db):
        return ids.pop(0)

    monkeypatch.setattr("app.engine.match_creation.allocate_match_id", _allocate)

    match = await create_match(
        session,
        game="hoard-hurt-help",
        name="New",
        scheduled_start=future,
        min_players=1,
        max_players=3,
        per_turn_deadline_seconds=60,
        total_rounds=7,
        turns_per_round=7,
        state=GameState.REGISTERING,
        commit=True,
    )

    assert match.id == "M_0002"
    rows = (
        await session.execute(select(Match).order_by(Match.id))
    ).scalars().all()
    assert [row.id for row in rows] == ["M_0001", "M_0002"]
