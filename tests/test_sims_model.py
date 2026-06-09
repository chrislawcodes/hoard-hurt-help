"""Smoke tests for the Sims storage defaults."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.db import make_engine
from app.engine.bot_presets import HISTORICAL_BOT_NAME_POOL
from app.models import Agent, AgentKind, Base, Match, GameState
from tests.factories import make_agent, make_user


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


def test_historical_sim_name_pool_has_curated_display_safe_names() -> None:
    assert len(HISTORICAL_BOT_NAME_POOL) == 125
    assert len(set(HISTORICAL_BOT_NAME_POOL)) == len(HISTORICAL_BOT_NAME_POOL)
    assert all("_" not in name for name in HISTORICAL_BOT_NAME_POOL)
    assert all(
        re.fullmatch(r"[A-Za-z0-9 ]{1,32}", name)
        for name in HISTORICAL_BOT_NAME_POOL
    )


@pytest.mark.asyncio
async def test_game_defaults_to_twenty_player_cap(reset_db):
    async with reset_db() as db:
        g = Match(
            id="G_SIM",
            name="Sim Test",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.flush()
        assert g.max_players == 20


@pytest.mark.asyncio
async def test_make_agent_defaults_to_ai_and_keeps_sim_fields_empty(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(db, user, name="Atlas")
        await db.flush()

        assert agent.kind is AgentKind.AI


@pytest.mark.asyncio
async def test_make_agent_can_persist_sim_traits(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(
            db,
            user,
            name="SimAtlas",
            kind=AgentKind.BOT,
            sim_strategy="grudger",
            sim_truthfulness=80,
            sim_trust_model="bitter",
            sim_seed=42,
            sim_version="v1",
            sim_fixture_pack="fixture-a",
        )
        await db.flush()

        assert agent.kind is AgentKind.BOT
        assert agent.bot_strategy == "grudger"
        assert agent.bot_truthfulness == 80
        assert agent.bot_trust_model == "bitter"
        assert agent.bot_seed == 42
        assert agent.bot_version == "v1"
        assert agent.bot_fixture_pack == "fixture-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_kind", ["bot", "BOT"])
async def test_agent_kind_loads_legacy_storage_values(reset_db, stored_kind):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(db, user, name="Atlas")
        await db.execute(
            text("UPDATE agents SET kind = :kind WHERE id = :agent_id"),
            {"kind": stored_kind, "agent_id": agent.id},
        )
        await db.commit()

    async with reset_db() as db:
        loaded = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        assert loaded.kind is AgentKind.BOT


@pytest.mark.asyncio
async def test_make_agent_persists_lowercase_enum_value(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(db, user, name="Atlas")
        stored_kind = (
            await db.execute(text("SELECT kind FROM agents WHERE id = :agent_id"), {"agent_id": agent.id})
        ).scalar_one()

    assert stored_kind == "ai"
