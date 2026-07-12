"""Phase 7: agent public messages are censored (masked), not blocked."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, TurnSubmission
from tests.test_two_phase_segregation import _seed_two_phase_game


# Bespoke: also resets agent_api._last_pull for this file's polling tests, so it
# can't delegate to tests/conftest.py's shared reset_db.
@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_submit_masks_bad_words_in_public_text(reset_db, client):
    game, players, _resolved, open_turn = await _seed_two_phase_game(reset_db)

    resp = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": f"{open_turn.turn_token}:{players[0].agent_id}:{game.id}"},
        json={
            "turn_token": "open-token",
            "action": "HOARD",
            "target_id": None,
            "message": "take that you shit",
            "thinking": "i will shit on them",
        },
        headers={"X-Connection-Key": players[0]._test_key},
    )
    assert resp.status_code == 202

    async with reset_db() as db:
        sub = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == open_turn.id,
                    TurnSubmission.player_id == players[0].id,
                )
            )
        ).scalar_one()

    # The message still posts (turn not blocked), but the bad word is censored.
    assert sub.message == "take that you ****"
    assert "shit" not in sub.thinking
    assert "****" in sub.thinking
