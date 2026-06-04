"""Routing tests for canonical match URLs and legacy redirects."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, GameState, Match


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


async def _seed_match(
    reset_db: async_sessionmaker,
    *,
    match_id: str = "M_001",
    state: GameState = GameState.REGISTERING,
) -> Match:
    async with reset_db() as db:
        match = Match(
            id=match_id,
            name="Test Match",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(match)
        await db.commit()
        await db.refresh(match)
        return match


@pytest.mark.asyncio
async def test_lobby_catalog_uses_canonical_games_path(client, reset_db):
    await _seed_match(reset_db)

    canonical = await client.get("/games/hoard-hurt-help")
    assert canonical.status_code == 200
    assert "Test Match" in canonical.text

    legacy = await client.get("/play/hoard-hurt-help", follow_redirects=False)
    assert legacy.status_code == 301
    assert legacy.headers["location"] == "/games/hoard-hurt-help"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_path, expected_location",
    [
        ("/games/G_001", "/games/hoard-hurt-help/matches/M_001"),
        ("/games/G_001/live", "/games/hoard-hurt-help/matches/M_001/live"),
        ("/games/G_001/analysis", "/games/hoard-hurt-help/matches/M_001/analysis"),
        (
            "/games/G_001/analysis/rounds/1",
            "/games/hoard-hurt-help/matches/M_001/analysis/rounds/1",
        ),
        ("/games/G_001/join", "/games/hoard-hurt-help/matches/M_001/join"),
    ],
)
async def test_legacy_match_urls_redirect_to_nested_paths(
    client,
    reset_db,
    legacy_path: str,
    expected_location: str,
):
    await _seed_match(reset_db, state=GameState.ACTIVE)

    r = await client.get(legacy_path, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == expected_location

    if legacy_path.endswith("/join"):
        post = await client.post(legacy_path, follow_redirects=False)
        assert post.status_code == 308
        assert post.headers["location"] == expected_location
