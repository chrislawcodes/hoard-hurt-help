"""Data-transform test for migration 0043 (clear legacy agent-version models).

The migration runs one statement; this seeds production-shaped rows and runs the
identical SQL to prove it (a) nulls legacy AI models regardless of which provider
they belong to, (b) preserves the ``'human'`` sentinel human seats use, and (c)
is idempotent. The schema round-trip itself is covered by test_migrations.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.models import Base
from app.models.agent import AgentKind
from app.models.agent_version import AgentVersion
from tests.factories import make_agent, make_user, make_version

# Mirrors migrations/versions/0043_clear_legacy_agent_model.py exactly.
BACKFILL_SQL = "UPDATE agent_versions SET model = NULL WHERE model IS NOT NULL AND model <> 'human'"


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


async def _model_of(db: AsyncSession, version_id: int) -> str | None:
    row = (
        await db.execute(select(AgentVersion.model).where(AgentVersion.id == version_id))
    ).one()
    return row[0]


@pytest.mark.asyncio
async def test_backfill_nulls_legacy_models_and_preserves_human(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session, 0)
    # A legacy AI agent whose stored model belongs to a DIFFERENT provider than a
    # Claude seat would pick — the exact shape that 404'd the claude CLI.
    _, gpt_ver = await make_agent(db_session, user, name="gpt-agent", model="gpt-5.4-mini")
    # A legacy AI agent whose model matches its own family — still legacy, still
    # cleared (new agents are model-less).
    _, claude_ver = await make_agent(
        db_session, user, name="claude-agent", model="claude-haiku-4-5"
    )
    # A human seat: model is the 'human' sentinel and must be preserved.
    # (make_agent only builds a version for AI agents, so add one explicitly.)
    human_agent, _ = await make_agent(
        db_session, user, name="human-agent", kind=AgentKind.HUMAN
    )
    human_ver = await make_version(db_session, human_agent, model="human")
    # An already-decoupled agent: model is already NULL, untouched.
    _, null_ver = await make_agent(db_session, user, name="null-agent", model=None)
    await db_session.flush()

    assert gpt_ver is not None and claude_ver is not None
    assert human_ver is not None and null_ver is not None
    # Capture ids before the raw UPDATE; a column select reads fresh from the DB.
    gpt_id, claude_id, human_id, null_id = (
        gpt_ver.id,
        claude_ver.id,
        human_ver.id,
        null_ver.id,
    )

    await db_session.execute(text(BACKFILL_SQL))

    assert await _model_of(db_session, gpt_id) is None
    assert await _model_of(db_session, claude_id) is None
    assert await _model_of(db_session, human_id) == "human"
    assert await _model_of(db_session, null_id) is None


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    _, gpt_ver = await make_agent(db_session, user, model="gpt-5.4-mini")
    await db_session.flush()
    assert gpt_ver is not None
    gpt_id = gpt_ver.id

    first = await db_session.execute(text(BACKFILL_SQL))
    assert first.rowcount == 1  # the one legacy row
    second = await db_session.execute(text(BACKFILL_SQL))
    assert second.rowcount == 0  # nothing left to clear
    assert await _model_of(db_session, gpt_id) is None
