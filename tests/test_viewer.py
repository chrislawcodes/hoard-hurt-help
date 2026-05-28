"""Game viewer + SSE + spectator API tests."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.broadcast import publish
from app.main import app
from app.models import Base, Game, GameState, Player, StrategyPrompt, User


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


async def _seed(reset_db, state=GameState.ACTIVE):
    async with reset_db() as db:
        u = User(google_sub="u", email="u@t.com")
        db.add(u)
        await db.flush()
        g = Game(
            id="G_001",
            name="Test",
            state=state,
            scheduled_start=datetime.now(timezone.utc),
            current_round=1,
            current_turn=1,
        )
        db.add(g)
        await db.flush()
        p = Player(
            game_id="G_001",
            user_id=u.id,
            agent_id="AI_0",
            agent_key_hash="x",
        )
        db.add(p)
        await db.flush()
        db.add(
            StrategyPrompt(
                player_id=p.id,
                prompt_text="SECRET STRATEGY DO NOT LEAK",
                is_default=False,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_viewer_renders_active(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "Test" in r.text


@pytest.mark.asyncio
async def test_viewer_does_not_leak_strategy(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "SECRET STRATEGY" not in r.text


@pytest.mark.asyncio
async def test_spectator_state_no_prompts(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    # Schema has no strategy field; verify by absence.
    assert "strategy_prompt" not in r.text
    assert body["name"] == "Test"


@pytest.mark.asyncio
async def test_completed_viewer_has_timeline(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "timeline" in r.text  # scrubber script wired
