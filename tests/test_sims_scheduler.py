"""Scheduler integration tests for deterministic Sims."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as app_db
from app.engine import scheduler
from app.models import Base, BotKind, Game, GameState, Player, Turn, TurnMessage, TurnSubmission, User
from tests.factories import make_bot


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
    monkeypatch.setattr(app_db, "SessionLocal", test_factory)
    monkeypatch.setattr(scheduler, "SessionLocal", test_factory)
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def db(reset_db):
    async with reset_db() as session:
        yield session


@pytest.fixture
def published(monkeypatch):
    events: list[tuple[str, str, dict]] = []

    async def fake_publish(channel: str, event_type: str, payload: dict) -> None:
        events.append((channel, event_type, payload))

    monkeypatch.setattr(scheduler, "publish", fake_publish)
    return events


async def _seed_sim_game(db: AsyncSession) -> tuple[Game, list[Player]]:
    game = Game(
        id="G_SIM",
        name="sim-game",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        total_rounds=1,
        turns_per_round=1,
    )
    db.add(game)
    await db.flush()

    players: list[Player] = []
    sim_specs = [
        ("AI_00", "leader_pressure", 80, "even", 11),
        ("AI_01", "diplomat", 80, "open", 12),
    ]
    for i, (agent_id, strategy, truthfulness, trust_model, seed) in enumerate(sim_specs):
        user = User(google_sub=f"sub-{i}", email=f"sim{i}@test.com", name=f"sim{i}")
        db.add(user)
        await db.flush()
        bot, _key = await make_bot(
            db,
            user,
            name=f"bot-{agent_id}",
            kind=BotKind.SIM,
            sim_strategy=strategy,
            sim_truthfulness=truthfulness,
            sim_trust_model=trust_model,
            sim_seed=seed,
            sim_version="v1",
        )
        player = Player(
            game_id=game.id,
            user_id=user.id,
            bot_id=bot.id,
            agent_id=agent_id,
        )
        db.add(player)
        await db.flush()
        players.append(player)

    await db.commit()
    return game, players


@pytest.mark.asyncio
async def test_scheduler_auto_submits_sim_talk_and_actions(db, published):
    game, players = await _seed_sim_game(db)

    await asyncio.wait_for(scheduler._run_game(game.id), timeout=5)

    async with scheduler.SessionLocal() as fresh_db:
        turn = (
            await fresh_db.execute(select(Turn).where(Turn.game_id == game.id))
        ).scalar_one()
        messages = (
            await fresh_db.execute(select(TurnMessage).where(TurnMessage.turn_id == turn.id))
        ).scalars().all()
        submissions = (
            await fresh_db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        ).scalars().all()
        fresh_game = (
            await fresh_db.execute(select(Game).where(Game.id == game.id))
        ).scalar_one()

    assert fresh_game.state is GameState.COMPLETED
    assert turn.talk_resolved_at is not None
    assert turn.resolved_at is not None
    assert len(messages) == len(players)
    assert len(submissions) == len(players)
    assert all(m.was_defaulted is False for m in messages)
    assert all(s.was_defaulted is False for s in submissions)
    assert all(m.text for m in messages)
    assert all(s.action in {"HOARD", "HELP", "HURT"} for s in submissions)
    assert [event for _, event, _ in published[:3]] == [
        "turn_opened",
        "turn_talked",
        "turn_opened",
    ]
