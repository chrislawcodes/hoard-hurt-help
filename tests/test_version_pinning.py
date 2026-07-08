"""The seat's pinned version is what plays.

Joining stamps ``Player.agent_version_id``; match start re-stamps it from the
agent's ``current_version_id`` in the same transaction as the ACTIVE flip; turn
serving resolves the strategy through that pin. So a pre-start edit is picked up
at start, and a mid-match restore-version only affects future matches.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import make_engine
from app.engine.agent_play_next_turn import agent_identity_for, get_next_turn
from app.engine.scheduler import registry, start_game
from app.models import Base
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection
from app.models.match import GameState, Match
from app.models.player import Player
from app.routes.agents_lifecycle import router as agents_lifecycle_router
from app.routes.web_join import _seat_user_agent
from tests.conftest import signed_in_cookies as _signed_in_cookies
from tests.factories import (
    make_agent,
    make_connection,
    make_match,
    make_turn,
    make_user,
    make_version,
    seat_prebuilt_player,
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite:///:memory:")
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
    test_app = FastAPI()
    test_app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=False,
        session_cookie="hhh_session",
    )
    test_app.include_router(agents_lifecycle_router, prefix="/me/agents")
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def no_game_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep start_game from spawning a real asyncio turn-loop task."""
    monkeypatch.setattr(registry, "start", lambda match_id: None)


async def _completed_rated_seat(db: AsyncSession, *, user, agent, version, match_id: str) -> None:
    """Give *version* rated history: a seat in an already-completed rated match
    (so the next edit forks instead of updating the draft in place)."""
    now = datetime.now(timezone.utc)
    match = await make_match(
        db,
        match_id,
        state=GameState.COMPLETED,
        scheduled_start=now - timedelta(hours=2),
        started_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=1),
    )
    await seat_prebuilt_player(
        db, match=match, user=user, agent=agent, version=version, seat_name="history-seat"
    )


async def test_match_start_restamps_pin_and_serves_forked_version(
    session_factory: async_sessionmaker[AsyncSession],
    no_game_loop: None,
) -> None:
    """Join pins the current version; a pre-start fork moves the pointer; match
    start re-stamps the pin; the served turn carries the forked text."""
    from app.routes.agents_lifecycle import _apply_version_edit

    async with session_factory() as db:
        user = await make_user(db, i=0)
        # Serving calls the engine directly (no HTTP auth to stamp last_seen_at),
        # so mark the connection recently seen or routing treats it as dead.
        connection, _key = await make_connection(
            db, user, last_seen_at=datetime.now(timezone.utc)
        )
        agent, v1 = await make_agent(db, user, connection=connection, name="Pinner", strategy_text="old text v1")
        assert v1 is not None
        await _completed_rated_seat(db, user=user, agent=agent, version=v1, match_id="M_HIST")

        match = await make_match(
            db,
            "M_PIN",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        # Seat through the real join builder so the join-time pin is the code
        # path under test, then confirm the held seat (what sweep_held_seats
        # does the moment the chosen AI comes online).
        player = await _seat_user_agent(
            db, user, match, agent.id, set(), chosen_provider="claude"
        )
        player.seat_reserved_until = None
        db.add(player)
        await db.commit()
        assert player.agent_version_id == v1.id  # join pinned the current version

        # Pre-start edit: v1 has rated history, so this forks v2 and moves the
        # agent's current pointer — the seat pin deliberately stays at v1.
        v2 = await _apply_version_edit(db, agent=agent, strategy_text="new text v2")
        await db.commit()
        assert v2.version_no == 2
        assert agent.current_version_id == v2.id
        assert player.agent_version_id == v1.id

        await start_game(db, match)

        stored_player = (
            await db.execute(select(Player).where(Player.id == player.id))
        ).scalar_one()
        assert stored_player.agent_version_id == v2.id  # re-stamped at start

        await make_turn(db, match.id, phase="act", resolved=False)
        await db.commit()

        payload = await get_next_turn(db, connection, max_hold_seconds=0)
        assert payload["status"] == "your_turn"
        assert payload["version_no"] == 2
        assert payload["strategy"] == "new text v2"
        static = payload["static"]
        assert isinstance(static, dict)
        assert static["your_strategy"] == "new text v2"


async def test_midmatch_restore_does_not_change_what_the_match_is_served(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Restoring an old version mid-match moves the agent's current pointer, but
    the live match keeps serving the seat's pinned version."""
    async with session_factory() as db:
        user = await make_user(db, i=1)
        # Recently seen, as the HTTP auth path would stamp it (see test above).
        connection, _key = await make_connection(
            db, user, last_seen_at=datetime.now(timezone.utc)
        )
        agent, v1 = await make_agent(db, user, connection=connection, name="Restorer", strategy_text="old text v1")
        assert v1 is not None
        v2 = await make_version(db, agent, version_no=2, strategy_text="pinned text v2")

        now = datetime.now(timezone.utc)
        match = await make_match(
            db,
            "M_LIVE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=5),
            started_at=now - timedelta(minutes=5),
        )
        player = Player(
            match_id=match.id,
            user_id=user.id,
            agent_id=agent.id,
            agent_version_id=v2.id,  # the pin stamped at match start
            seat_name="Restorer",
            chosen_provider="claude",
        )
        db.add(player)
        await make_turn(db, match.id, phase="act", resolved=False)
        await db.commit()

    resp = await client.post(
        f"/me/agents/{agent.id}/restore-version/{v1.id}",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert resp.status_code == 303

    async with session_factory() as db:
        stored_agent = (
            await db.execute(select(Agent).where(Agent.id == agent.id))
        ).scalar_one()
        assert stored_agent.current_version_id == v1.id  # restore took effect

        stored_connection = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        payload = await get_next_turn(db, stored_connection, max_hold_seconds=0)
        assert payload["status"] == "your_turn"
        assert payload["strategy"] == "pinned text v2"
        assert payload["version_no"] == 2

        # The MCP instructions path resolves the same pin for a live match.
        _match, _seat, _targets, strategy_text = await agent_identity_for(
            db, stored_connection
        )
        assert strategy_text == "pinned text v2"


async def test_poller_autostart_restamps_pins_and_skips_versionless_agents(
    session_factory: async_sessionmaker[AsyncSession],
    no_game_loop: None,
) -> None:
    """The poller's auto-start path runs through the same start_game, so a due
    match re-stamps every seat; a seat whose agent has no current version keeps
    its (empty) pin."""
    async with session_factory() as db:
        user = await make_user(db, i=2)
        await make_connection(db, user)
        agent_a, v1_a = await make_agent(db, user, connection=None, name="Alpha", strategy_text="alpha v1")
        agent_b, v1_b = await make_agent(db, user, connection=None, name="Beta")
        agent_c, v1_c = await make_agent(db, user, connection=None, name="Gamma")
        assert v1_a is not None and v1_b is not None and v1_c is not None
        # Move Alpha's current pointer past the join-time pin (how it moved —
        # fork or restore — is irrelevant to the re-stamp).
        v2_a = await make_version(db, agent_a, version_no=2, strategy_text="alpha v2")
        # A scripted-bot seat has no versions at all; its pin must survive as-is.
        bot_agent, _ = await make_agent(db, user, kind=AgentKind.BOT, name="Bot")

        match = await make_match(
            db,
            "M_DUE",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        for agent, version, seat_name in (
            (agent_a, v1_a, "Alpha"),
            (agent_b, v1_b, "Beta"),
            (agent_c, v1_c, "Gamma"),
        ):
            await seat_prebuilt_player(
                db, match=match, user=user, agent=agent, version=version, seat_name=seat_name
            )
        db.add(
            Player(
                match_id=match.id,
                user_id=user.id,
                agent_id=bot_agent.id,
                agent_version_id=None,
                seat_name="Bot",
            )
        )
        await db.commit()

    started = await registry.start_due_games(session_factory=session_factory)
    assert started == 1

    async with session_factory() as db:
        stored_match = (
            await db.execute(select(Match).where(Match.id == "M_DUE"))
        ).scalar_one()
        assert stored_match.state == GameState.ACTIVE
        pins = dict(
            (
                await db.execute(
                    select(Player.seat_name, Player.agent_version_id).where(
                        Player.match_id == "M_DUE"
                    )
                )
            ).all()
        )
        assert pins["Alpha"] == v2_a.id  # re-stamped to the moved pointer
        assert pins["Beta"] == v1_b.id  # unchanged pointer, unchanged pin
        assert pins["Gamma"] == v1_c.id
        assert pins["Bot"] is None  # no current version → pin left as-is
