"""Smoke tests for the Sims storage defaults."""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import make_engine
from app.models import Base, BotKind, Game, GameState
from tests.factories import make_bot, make_user


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


@pytest.mark.asyncio
async def test_game_defaults_to_twenty_player_cap(reset_db):
    async with reset_db() as db:
        g = Game(
            id="G_SIM",
            name="Sim Test",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.flush()
        assert g.max_players == 20


@pytest.mark.asyncio
async def test_make_bot_defaults_to_external_and_keeps_sim_fields_empty(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        bot, _ = await make_bot(db, user, name="Atlas")
        await db.flush()

        assert bot.kind is BotKind.EXTERNAL
        assert bot.sim_strategy is None
        assert bot.sim_truthfulness is None
        assert bot.sim_trust_model is None
        assert bot.sim_seed is None
        assert bot.sim_version is None
        assert bot.sim_fixture_pack is None


@pytest.mark.asyncio
async def test_make_bot_can_persist_sim_traits(reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        bot, _ = await make_bot(
            db,
            user,
            name="SimAtlas",
            kind=BotKind.SIM,
            sim_strategy="grudger",
            sim_truthfulness=80,
            sim_trust_model="bitter",
            sim_seed=42,
            sim_version="v1",
            sim_fixture_pack="fixture-a",
        )
        await db.flush()

        assert bot.kind is BotKind.SIM
        assert bot.sim_strategy == "grudger"
        assert bot.sim_truthfulness == 80
        assert bot.sim_trust_model == "bitter"
        assert bot.sim_seed == 42
        assert bot.sim_version == "v1"
        assert bot.sim_fixture_pack == "fixture-a"
