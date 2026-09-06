"""Shared fixtures and DB-seeding helpers for the agent-next-turn fan-out tests.

Split across test_agent_next_turn_seat_routing.py, test_agent_next_turn_payload.py,
test_agent_next_turn_pacing.py, and test_agent_report_pid.py (formerly one file,
test_agent_next_turn_fanout.py).

The four fixtures below (``engine``, ``session_factory``, ``app``, ``scoped_client``)
are a deliberate override of the same-named fixtures in tests/conftest.py: they use a
FILE-backed SQLite database rather than ``:memory:`` (see the ``engine`` docstring for
why), and ``app`` wires up only the agent-facing routers these tests exercise. Every
file that imports any one of them must import all four, even the ones it never
references by name — pytest resolves ``session_factory``'s ``engine`` parameter (and
``scoped_client``'s ``app`` parameter) by fixture name, and if a file only imports the
fixtures it names directly, the missing links quietly fall back to conftest.py's plain
in-memory ``engine`` instead of this file-backed one. ``__all__`` below keeps ruff from
"cleaning up" the two that aren't otherwise referenced.

``make_agent``/``make_agent_version`` here are NOT the same helpers as
``tests.factories.make_agent`` — this file's ``make_agent`` returns a bare ``Agent``
(no version), while ``tests.factories.make_agent`` returns ``tuple[Agent, AgentVersion
| None]`` and takes a different keyword set. Do not import both under the name
``make_agent`` in one test file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.engine.tokens import generate_turn_token
from app.models import Base
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn
from app.models.user import User
from app.routes.agent_api import router as agent_api_router
from app.routes.agent_next_turn import router as agent_next_turn_router

__all__ = [
    "engine",
    "session_factory",
    "app",
    "scoped_client",
    "make_agent",
    "make_agent_version",
    "_create_match_with_turn",
    "_seat_agent",
]


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A FILE-backed SQLite database, deliberately not ``:memory:``.

    SQLAlchemy pools an in-memory SQLite with ``StaticPool`` — **one connection
    shared by every session**. That makes concurrent sessions destructive to one
    another: if session A rolls back while session B holds an uncommitted INSERT
    on that same connection, B's row is discarded and B's later ``commit()``
    still returns cleanly, so the write silently vanishes.

    These long-poll tests are exactly that shape. The hold re-checks on a loop
    and rolls back between ticks (to hand its pooled connection back) while a
    second session opens a turn partway through. Under ``:memory:`` the hold's
    rollback could wipe the turn before it was ever committed; the hold then
    correctly found nothing and answered "waiting", failing about one run in six.
    It reproduced only with the whole file running, and instrumenting it shifted
    the timing enough to hide it.

    A file URL gets ``AsyncAdaptedQueuePool`` — one connection per session, which
    is what production does on Postgres. So this also makes these tests exercise
    the isolation the real deployment has instead of an artefact of the pool.
    """
    eng = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def app(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})
    test_app = FastAPI()
    test_app.include_router(agent_api_router, prefix="/api/matches/{match_id}")
    test_app.include_router(agent_api_router, prefix="/api/games/{match_id}")
    test_app.include_router(agent_next_turn_router)
    return test_app


@pytest.fixture
async def scoped_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_agent(
    db: AsyncSession,
    user: User,
    *,
    connection: Connection | None = None,
    name: str | None = None,
    kind: AgentKind = AgentKind.AI,
) -> Agent:
    provider = (
        connection.provider
        if (kind == AgentKind.AI and connection is not None)
        else (ConnectionProvider.CLAUDE if kind == AgentKind.AI else None)
    )
    agent = Agent(
        user_id=user.id,
        provider=provider,
        kind=kind,
        name=name or f"agent-{user.id}",
    )
    db.add(agent)
    await db.flush()
    return agent


async def make_agent_version(
    db: AsyncSession,
    agent: Agent,
    *,
    version_no: int = 1,
    model: str = "claude-haiku-4-5",
    strategy_text: str = "Default strategy.",
) -> AgentVersion:
    agent_version = AgentVersion(
        agent_id=agent.id,
        version_no=version_no,
        model=model,
        strategy_text=strategy_text,
    )
    db.add(agent_version)
    await db.flush()
    return agent_version


async def _create_match_with_turn(
    db: AsyncSession,
    match_id: str,
    *,
    deadline_seconds: int,
    phase: str = "act",
) -> tuple[Match, Turn]:
    now = datetime.now(timezone.utc)
    match = Match(
        id=match_id,
        name=f"match-{match_id}",
        state=GameState.ACTIVE,
        scheduled_start=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
        per_turn_deadline_seconds=60,
        current_round=1,
        current_turn=1,
    )
    db.add(match)
    await db.flush()
    turn = Turn(
        match_id=match.id,
        round=1,
        turn=1,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=deadline_seconds),
        phase=phase,
    )
    db.add(turn)
    await db.flush()
    return match, turn


async def _seat_agent(
    db: AsyncSession,
    *,
    user,
    connection: Connection,
    match: Match,
    seat_name: str,
    agent_name: str,
    model: str,
    strategy_text: str,
    version_no: int = 1,
) -> tuple[Agent, AgentVersion, Player]:
    agent = await make_agent(db, user, connection=connection, name=agent_name)
    version = await make_agent_version(
        db,
        agent,
        version_no=version_no,
        model=model,
        strategy_text=strategy_text,
    )
    agent.current_version_id = version.id
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        # The seat is joined with the connection's AI; routing matches it.
        chosen_provider=connection.provider.value if connection.provider else None,
        model_self_report=model,
    )
    db.add(player)
    await db.flush()
    return agent, version, player
