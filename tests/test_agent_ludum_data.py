"""The Agent Ludum front page shows only real data — never fabricated rows.

The design handoff shipped fictional ELO ratings, @owner handles, and a
"find a rival in ~3s" promise. The page must show none of that: real standings
from a real game, or an honest empty state.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import Base, Game, GameState
from tests.factories import seat_player


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine

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


def _assert_no_fabricated_marketing_claims(text: str) -> None:
    """None of the prototype's invented content may reach the page."""
    assert "ELO" not in text
    assert "matchmaking" not in text.lower()
    assert "rival in" not in text.lower()


@pytest.mark.asyncio
async def test_empty_state_has_no_fabricated_rows(client, reset_db):
    """With zero games the page still renders, with an honest empty state."""
    r = await client.get("/")
    assert r.status_code == 200
    _assert_no_fabricated_marketing_claims(r.text)
    # Honest empty copy for the standings band, not invented leaderboard rows.
    assert "No games have been scored yet" in r.text


@pytest.mark.asyncio
async def test_standings_band_shows_real_agents(client, reset_db):
    """A finished showcase game's real agents appear in the standings band."""
    async with reset_db() as db:
        g = Game(
            id="G_done",
            name="Final Showdown",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc) - timedelta(hours=2),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        # A showcase game needs a full table (>= 3 active players).
        for i, (agent, score) in enumerate(
            [("Claudius", 22), ("Sonnet_Sue", 17), ("GPT_Greg", 9)]
        ):
            p = await seat_player(db, "G_done", agent_id=agent, i=i)
            p.current_round_score = score
        await db.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Claudius" in r.text  # a real agent_id, straight from the DB
    _assert_no_fabricated_marketing_claims(r.text)
