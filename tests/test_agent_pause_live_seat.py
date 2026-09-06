"""Pausing an agent that still holds a seat warns before it takes effect.

The bug this pins: every turn-serving query filters on ``status == ACTIVE``
(``playable_agent_filter``), so flipping an agent to PAUSED while it is seated
stops it being served turns. The seat stays in the match, and the overdue
sweeper defaults its moves for the rest of it. Nothing told the owner that.

The fix warns instead of blocking — someone whose AI has gone wrong still has
to be able to stop it — so these tests assert the round trip: first request
warns and changes nothing, the confirmed request pauses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.agent import Agent, AgentStatus
from app.models.match import GameState
from app.routes.agents_detail import router as agents_detail_router
from app.routes.agents_lifecycle import router as agents_lifecycle_router
from tests.conftest import make_scoped_app
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, make_match, make_user, seat_prebuilt_player


@pytest.fixture
async def session_factory(db_factory: async_sessionmaker) -> async_sessionmaker:
    """Alias for tests/conftest.py's db_factory, kept for this file's existing
    session_factory-named call sites."""
    return db_factory


@pytest.fixture
async def app_with_agent_lifecycle_and_detail_routes(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.db.engine", engine)
    # Both routers on purpose: the warning is a redirect from the lifecycle
    # router into a page rendered by the detail router, so a test that only
    # mounted one could not follow the round trip a user actually takes.
    return make_scoped_app(
        (agents_lifecycle_router, "/me/agents"),
        (agents_detail_router, "/me/agents"),
    )


@pytest.fixture
async def scoped_client(
    app_with_agent_lifecycle_and_detail_routes: FastAPI,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_agent_lifecycle_and_detail_routes)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _stored_status(
    session_factory: async_sessionmaker[AsyncSession], agent_id: int
) -> AgentStatus:
    async with session_factory() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one()
        return agent.status


async def test_pause_with_no_live_seat_pauses_immediately(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The common case keeps zero friction: no seat, one click, paused."""
    async with session_factory() as db:
        user = await make_user(db)
        agent, _ = await make_agent(db, user, name="Idle", status=AgentStatus.ACTIVE)
        await db.commit()
        user_id, agent_id = user.id, agent.id

    r = await scoped_client.post(f"/me/agents/{agent_id}/pause", cookies=_cookies(user_id))

    assert r.status_code == 303
    assert r.headers["location"] == f"/me/agents/{agent_id}"
    assert "pause_live_seats" not in r.headers["location"]
    assert await _stored_status(session_factory, agent_id) is AgentStatus.PAUSED


async def test_pause_with_live_seat_warns_and_does_not_pause(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Seated in a live match: the first request only warns, and the rendered
    page names the count and the real consequence."""
    async with session_factory() as db:
        user = await make_user(db)
        agent, version = await make_agent(
            db, user, name="Playing", status=AgentStatus.ACTIVE
        )
        assert version is not None
        match = await make_match(
            db,
            "M_LIVE1",
            state=GameState.ACTIVE,
            started_at=datetime.now(timezone.utc),
        )
        await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="s1"
        )
        await db.commit()
        user_id, agent_id = user.id, agent.id

    r = await scoped_client.post(f"/me/agents/{agent_id}/pause", cookies=_cookies(user_id))

    assert r.status_code == 303
    assert r.headers["location"] == f"/me/agents/{agent_id}?pause_live_seats=1"
    # The whole point: nothing changed yet.
    assert await _stored_status(session_factory, agent_id) is AgentStatus.ACTIVE

    page = await scoped_client.get(r.headers["location"], cookies=_cookies(user_id))
    assert page.status_code == 200
    body = page.text
    assert "Pause this agent?" in body
    assert "1 match that hasn't finished" in body
    # The consequence, not just the fact that something will happen.
    assert "it gets no more turns" in body
    # And a way through — this warns, it does not block.
    assert f'action="/me/agents/{agent_id}/pause?confirm=true"' in body
    assert "Pause anyway" in body


async def test_confirmed_pause_goes_through(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Confirming pauses the agent even though the seat is still live."""
    async with session_factory() as db:
        user = await make_user(db)
        agent, version = await make_agent(
            db, user, name="Playing", status=AgentStatus.ACTIVE
        )
        assert version is not None
        match = await make_match(
            db,
            "M_LIVE2",
            state=GameState.ACTIVE,
            started_at=datetime.now(timezone.utc),
        )
        await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="s1"
        )
        await db.commit()
        user_id, agent_id = user.id, agent.id

    r = await scoped_client.post(
        f"/me/agents/{agent_id}/pause?confirm=true", cookies=_cookies(user_id)
    )

    assert r.status_code == 303
    assert r.headers["location"] == f"/me/agents/{agent_id}"
    assert await _stored_status(session_factory, agent_id) is AgentStatus.PAUSED


async def test_seat_count_covers_every_unfinished_state_and_ignores_the_rest(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Three unfinished matches count; a finished one and a seat already left
    do not. A SCHEDULED or REGISTERING seat is exposed to the same damage as an
    ACTIVE one — the match starts and the paused agent is served nothing."""
    async with session_factory() as db:
        user = await make_user(db)
        agent, version = await make_agent(
            db, user, name="Busy", status=AgentStatus.ACTIVE
        )
        assert version is not None
        seats = {
            "M_ACT": GameState.ACTIVE,
            "M_REG": GameState.REGISTERING,
            "M_SCH": GameState.SCHEDULED,
            "M_DONE": GameState.COMPLETED,
            "M_LEFT": GameState.ACTIVE,
        }
        for match_id, state in seats.items():
            match = await make_match(db, match_id, state=state)
            player = await seat_prebuilt_player(
                db,
                match=match,
                user=user,
                agent=agent,
                version=version,
                seat_name="s1",
            )
            if match_id == "M_LEFT":
                player.left_at = datetime.now(timezone.utc)
        await db.commit()
        user_id, agent_id = user.id, agent.id

    r = await scoped_client.post(f"/me/agents/{agent_id}/pause", cookies=_cookies(user_id))

    assert r.status_code == 303
    assert r.headers["location"] == f"/me/agents/{agent_id}?pause_live_seats=3"
    assert await _stored_status(session_factory, agent_id) is AgentStatus.ACTIVE

    page = await scoped_client.get(r.headers["location"], cookies=_cookies(user_id))
    assert page.status_code == 200
    assert "3 matches that haven't finished" in page.text


async def test_detail_page_has_no_warning_without_the_redirect(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The warning belongs to the pause round trip only — a seated agent's page
    must not nag on every normal visit."""
    async with session_factory() as db:
        user = await make_user(db)
        agent, version = await make_agent(
            db, user, name="Playing", status=AgentStatus.ACTIVE
        )
        assert version is not None
        match = await make_match(
            db,
            "M_LIVE3",
            state=GameState.ACTIVE,
            started_at=datetime.now(timezone.utc),
        )
        await seat_prebuilt_player(
            db, match=match, user=user, agent=agent, version=version, seat_name="s1"
        )
        await db.commit()
        user_id, agent_id = user.id, agent.id

    page = await scoped_client.get(f"/me/agents/{agent_id}", cookies=_cookies(user_id))

    assert page.status_code == 200
    assert "Pause this agent?" not in page.text
