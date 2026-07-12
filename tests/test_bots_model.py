"""Smoke tests for the bots storage defaults."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.bot_presets import HISTORICAL_BOT_NAME_POOL
from app.models import Agent, AgentKind, Match, GameState
from tests.factories import make_agent, make_user


@pytest.fixture(autouse=True)
async def reset_db(reset_db: async_sessionmaker) -> async_sessionmaker:
    """Autouse override of tests/conftest.py's reset_db: every test here touches the DB."""
    return reset_db


def test_historical_bot_name_pool_has_curated_display_safe_names() -> None:
    assert len(HISTORICAL_BOT_NAME_POOL) == 125
    assert len(set(HISTORICAL_BOT_NAME_POOL)) == len(HISTORICAL_BOT_NAME_POOL)
    assert all("_" not in name for name in HISTORICAL_BOT_NAME_POOL)
    assert all(
        re.fullmatch(r"[A-Za-z0-9 ]{1,32}", name)
        for name in HISTORICAL_BOT_NAME_POOL
    )


async def test_game_defaults_to_ten_player_cap(reset_db):
    async with reset_db() as db:
        g = Match(
            id="G_BOT",
            name="Bot Test",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.flush()
        assert g.max_players == 10


async def test_make_agent_defaults_to_ai_and_keeps_bot_fields_empty(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(db, user, name="Atlas")
        await db.flush()

        assert agent.kind is AgentKind.AI


async def test_make_agent_can_persist_bot_traits(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(
            db,
            user,
            name="BotAtlas",
            kind=AgentKind.BOT,
            bot_strategy="grudger",
            bot_truthfulness=80,
            bot_trust_model="bitter",
            bot_seed=42,
            bot_version="v1",
            bot_fixture_pack="fixture-a",
        )
        await db.flush()

        assert agent.kind is AgentKind.BOT
        assert agent.bot_strategy == "grudger"
        assert agent.bot_truthfulness == 80
        assert agent.bot_trust_model == "bitter"
        assert agent.bot_seed == 42
        assert agent.bot_version == "v1"
        assert agent.bot_fixture_pack == "fixture-a"


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


async def test_make_agent_persists_lowercase_enum_value(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        agent, _ = await make_agent(db, user, name="Atlas")
        stored_kind = (
            await db.execute(text("SELECT kind FROM agents WHERE id = :agent_id"), {"agent_id": agent.id})
        ).scalar_one()

    assert stored_kind == "ai"
