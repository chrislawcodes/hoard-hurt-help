"""Leaderboard read-model tests for the owner-handle credit."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, GameState, Match, Player
from app.models.agent import AgentKind
from app.read_models.leaderboard import load_leaderboard_sections
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


async def _seed_completed_match(reset_db) -> None:
    """One completed match: an agent with a handle, an agent without, and a Sim."""
    async with reset_db() as db:
        user_with = await make_user(db, 1)  # factory gives handle "agent1"
        user_without = await make_user(db, 2)
        user_without.handle = None
        user_without.handle_key = None
        sim_owner = await make_user(db, 3)

        agent_with, version_with = await make_agent(db, user_with, name="AliceBot")
        agent_without, version_without = await make_agent(db, user_without, name="BobBot")
        sim_bot, _ = await make_agent(
            db,
            sim_owner,
            name="Coalition Seeker",
            kind=AgentKind.BOT,
            sim_profile_name="Coalition Seeker",
            sim_strategy="coalition_seeker",
        )

        match = Match(
            id="M_lb1",
            name="Ranked Match",
            state=GameState.COMPLETED,
            scheduled_start=datetime(2026, 6, 4, tzinfo=timezone.utc),
            per_turn_deadline_seconds=60,
            game="hoard-hurt-help",
        )
        db.add(match)
        await db.flush()
        db.add_all(
            [
                Player(
                    match_id=match.id, user_id=user_with.id,
                    agent_id=agent_with.id,
                    seat_name="AliceBot",
                    agent_version_id=version_with.id if version_with else None,
                    total_round_wins=3, total_round_score=30,
                    model_self_report=version_with.model if version_with else None,
                ),
                Player(
                    match_id=match.id, user_id=user_without.id,
                    agent_id=agent_without.id,
                    seat_name="BobBot",
                    agent_version_id=version_without.id if version_without else None,
                    total_round_wins=1, total_round_score=10,
                    model_self_report=version_without.model if version_without else None,
                ),
                Player(
                    match_id=match.id, user_id=sim_owner.id,
                    agent_id=sim_bot.id,
                    seat_name="Coalition Seeker",
                    total_round_wins=2, total_round_score=20,
                ),
            ]
        )
        await db.commit()


async def test_owner_handle_shown_for_agents_and_absent_for_sims(reset_db):
    await _seed_completed_match(reset_db)
    async with reset_db() as db:
        sections = await load_leaderboard_sections(db, included="all")

    rows = {row.display_name: row for section in sections for row in section.rows}

    alice = next(row for name, row in rows.items() if name.startswith("AliceBot"))
    bob = next(row for name, row in rows.items() if name.startswith("BobBot"))

    assert alice.owner_handle == "agent1"
    # Agent whose owner has not picked a handle yet: no credit.
    assert bob.owner_handle is None
    # Sim still appears (the User join didn't drop it) but carries no owner.
    assert rows["Coalition Seeker"].is_bot is True
    assert rows["Coalition Seeker"].owner_handle is None


async def test_agents_view_keeps_handles_and_excludes_sims(reset_db):
    await _seed_completed_match(reset_db)
    async with reset_db() as db:
        sections = await load_leaderboard_sections(db, included="agents")

    rows = {row.display_name: row for section in sections for row in section.rows}

    assert "Coalition Seeker" not in rows
    alice = next(row for name, row in rows.items() if name.startswith("AliceBot"))
    bob = next(row for name, row in rows.items() if name.startswith("BobBot"))
    assert alice.owner_handle == "agent1"
    assert bob.owner_handle is None


async def test_leaderboard_page_renders_owner_credit(reset_db, client):
    await _seed_completed_match(reset_db)
    resp = await client.get("/leaderboard")
    assert resp.status_code == 200
    assert "AliceBot" in resp.text
    assert "by @agent1" in resp.text
