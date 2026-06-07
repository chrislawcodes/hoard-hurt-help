"""Owner credit on the viewer: rc_data owners map + winner card."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, GameState, Match, Player
from app.routes.viewer_presentation import _build_rc_data
from tests.factories import make_agent, make_user


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_build_rc_data_includes_owner_map() -> None:
    scoreboard = [
        {"agent_id": "Napoleon", "round_score": 0, "round_wins": 0, "owner_handle": "alice"},
        {"agent_id": "SimX", "round_score": 0, "round_wins": 0, "owner_handle": None},
    ]
    data = json.loads(_build_rc_data(scoreboard, []))
    assert data["owners"] == {"Napoleon": "alice"}  # None owner omitted


async def test_viewer_shows_winner_owner_and_rail_data(reset_db, client):
    async with reset_db() as db:
        ua = await make_user(db, 1)  # handle "agent1"
        ub = await make_user(db, 2)  # handle "agent2"
        bot_a, _ = await make_agent(db, ua, name="AliceBot")
        bot_b, _ = await make_agent(db, ub, name="BobBot")
        match = Match(
            id="M_v1",
            name="Viewer Match",
            state=GameState.COMPLETED,
            scheduled_start=datetime(2026, 6, 4, tzinfo=timezone.utc),
            per_turn_deadline_seconds=60,
            game="hoard-hurt-help",
        )
        db.add(match)
        await db.flush()
        pa = Player(match_id=match.id, user_id=ua.id, agent_id=bot_a.id, seat_name="Napoleon")
        pb = Player(match_id=match.id, user_id=ub.id, agent_id=bot_b.id, seat_name="Wellington")
        db.add_all([pa, pb])
        await db.flush()
        match.winner_player_id = pa.id
        await db.commit()

    resp = await client.get("/games/hoard-hurt-help/matches/M_v1")
    assert resp.status_code == 200
    # Winner card credit (server-rendered).
    assert "run by @agent1" in resp.text
    # rc_data owners map embedded for the JS-built rail.
    assert '"owners"' in resp.text
    assert "agent2" in resp.text
