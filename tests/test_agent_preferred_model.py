"""The Agent.preferred_model column persists and defaults to NULL.

Covers the storage half of the per-agent model slice (the resolution logic lives
in test_model_provider_match.py; the migration round-trip in test_migrations.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.models import Base
from app.models.agent import Agent
from tests.factories import make_agent, make_user


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _preferred_model_of(db: AsyncSession, agent_id: int) -> str | None:
    row = (
        await db.execute(select(Agent.preferred_model).where(Agent.id == agent_id))
    ).one()
    return row[0]


@pytest.mark.asyncio
async def test_preferred_model_defaults_to_null(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    agent, _ = await make_agent(db_session, user, name="default-agent")
    await db_session.flush()
    assert await _preferred_model_of(db_session, agent.id) is None


@pytest.mark.asyncio
async def test_preferred_model_persists_when_set(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    agent, _ = await make_agent(db_session, user, name="opus-agent")
    agent.preferred_model = "claude-opus-4-8"
    await db_session.flush()
    agent_id = agent.id
    assert await _preferred_model_of(db_session, agent_id) == "claude-opus-4-8"
