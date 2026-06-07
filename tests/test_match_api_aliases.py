"""API alias tests for canonical match IDs and legacy game IDs."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, GameState, Match
from tests.factories import seat_player


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_match_with_player(
    reset_db: async_sessionmaker,
) -> tuple[str, str]:
    async with reset_db() as db:
        match = Match(
            id="M_001",
            name="Alias Test",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(match)
        await db.flush()
        player = await seat_player(db, "M_001", "AI_0")
        key = getattr(player, "_test_key")
        await db.commit()
        return "M_001", key


@pytest.mark.asyncio
async def test_agent_state_accepts_canonical_and_legacy_prefixes(client, reset_db):
    match_id, agent_key = await _seed_match_with_player(reset_db)
    headers = {"X-Connection-Key": agent_key}

    canonical = await client.get(f"/api/matches/{match_id}/state", headers=headers)
    legacy = await client.get(f"/api/games/{match_id}/state", headers=headers)

    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert canonical.json() == legacy.json()
    assert canonical.json()["match_id"] == match_id
    assert canonical.json()["game_id"] == match_id


@pytest.mark.asyncio
async def test_spectator_state_accepts_canonical_and_legacy_prefixes(client, reset_db):
    match_id, _ = await _seed_match_with_player(reset_db)

    canonical = await client.get(f"/api/spectator/matches/{match_id}/state")
    legacy = await client.get(f"/api/spectator/games/{match_id}/state")

    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert canonical.json() == legacy.json()
    assert canonical.json()["match_id"] == match_id
    assert canonical.json()["game_id"] == match_id
