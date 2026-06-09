from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import make_engine
from app.engine.tokens import bot_key_lookup, generate_connection_key
from app.models import Base
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_setup import ConnectionSetup
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.models.user import User
from app.routes.agents_lifecycle import router as agents_lifecycle_router
from app.routes.agents_setup import router as agents_setup_router
from app.routes.agents_status import router as agents_status_router
from app.routes.connections_credentials import router as connections_credentials_router
from app.routes.connections_lifecycle import router as connections_lifecycle_router
from app.routes.connections_setup import router as connections_setup_router


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
    test_app.include_router(agents_setup_router, prefix="/me/agents")
    test_app.include_router(agents_status_router, prefix="/me/agents")
    test_app.include_router(agents_lifecycle_router, prefix="/me/agents")
    test_app.include_router(connections_setup_router, prefix="/me/connections")
    test_app.include_router(connections_credentials_router, prefix="/me/connections")
    test_app.include_router(connections_lifecycle_router, prefix="/me/connections")
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _signed_in_cookies(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(
        json.dumps({"user_id": user_id, "next_after_login": None}).encode()
    ).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _make_user(db: AsyncSession, *, handle: str, i: int) -> User:
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@example.com",
        handle=handle,
        handle_key=handle,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_connection(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    nickname: str | None = None,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    max_concurrent_games: int = 3,
    key: str | None = None,
) -> tuple[Connection, str]:
    plain_key = key or generate_connection_key()
    connection = Connection(
        user_id=user.id,
        nickname=nickname,
        provider=provider,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=plain_key[-4:],
        status=status,
        max_concurrent_games=max_concurrent_games,
    )
    db.add(connection)
    await db.flush()
    return connection, plain_key


async def _make_agent(
    db: AsyncSession,
    user: User,
    *,
    connection: Connection | None,
    name: str,
    status: AgentStatus = AgentStatus.ACTIVE,
) -> Agent:
    agent = Agent(
        user_id=user.id,
        connection_id=None if connection is None else connection.id,
        kind=AgentKind.AI,
        name=name,
        game="hoard-hurt-help",
        status=status,
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_version(
    db: AsyncSession,
    agent: Agent,
    *,
    version_no: int = 1,
    model: str = "claude-haiku-4-5",
    strategy_text: str = "Play to win.",
) -> AgentVersion:
    version = AgentVersion(
        agent_id=agent.id,
        version_no=version_no,
        model=model,
        strategy_text=strategy_text,
    )
    db.add(version)
    await db.flush()
    agent.current_version_id = version.id
    await db.flush()
    return version


async def _make_match(
    db: AsyncSession, match_id: str, *, state: GameState, match_kind: str = "manual"
) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game="hoard-hurt-help",
        state=state,
        scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
        started_at=datetime.now(timezone.utc) - timedelta(hours=1)
        if state != GameState.SCHEDULED
        else None,
        completed_at=datetime.now(timezone.utc) if state == GameState.COMPLETED else None,
        per_turn_deadline_seconds=60,
        match_kind=match_kind,
    )
    db.add(match)
    await db.flush()
    return match


async def _seat_player(
    db: AsyncSession,
    *,
    match: Match,
    user: User,
    agent: Agent,
    version: AgentVersion,
    seat_name: str,
) -> Player:
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        model_self_report=version.model,
    )
    db.add(player)
    await db.flush()
    return player


@pytest.mark.asyncio
async def test_create_connection_reuses_existing_pending_setup(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent0", i=0)
        await db.commit()

    cookies = _signed_in_cookies(user.id)
    first = await client.post(
        "/me/connections",
        cookies=cookies,
        data={"provider": "claude", "nickname": "My Claude"},
        follow_redirects=True,
    )
    assert first.status_code == 200
    assert "agentludum_connector.py" in first.text
    assert "X-Connection-Key" in first.text
    first_match = re.search(r"--key (sk_conn_[a-f0-9]+) --url", first.text)
    assert first_match is not None
    first_key = first_match.group(1)

    second = await client.post(
        "/me/connections",
        cookies=cookies,
        data={"provider": "claude", "nickname": "My Claude 2"},
        follow_redirects=True,
    )
    assert second.status_code == 200
    second_match = re.search(r"--key (sk_conn_[a-f0-9]+) --url", second.text)
    assert second_match is not None
    second_key = second_match.group(1)
    assert second_key != first_key

    async with session_factory() as db:
        setups = (
            await db.execute(
                select(ConnectionSetup)
                .where(ConnectionSetup.user_id == user.id)
                .order_by(ConnectionSetup.id)
            )
        ).scalars().all()
        assert len(setups) == 1
        setup = setups[0]
        assert setup.nickname == "My Claude 2"
        assert setup.completed_at is None
        assert setup.connection_id is None
        assert setup.key_lookup == bot_key_lookup(second_key)
        assert setup.key_lookup != bot_key_lookup(first_key)


@pytest.mark.asyncio
async def test_new_agent_rejects_invalid_model_for_provider(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent1", i=1)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()

    resp = await client.post(
        "/me/agents/new",
        cookies=_signed_in_cookies(user.id),
        data={
            "connection_id": connection.id,
            "name": "Alpha",
            "model": "gpt-5.4-mini",
            "strategy_text": "Play to win.",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_version_edit_updates_draft_then_forks_after_rated_match(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db, handle="agent2", i=2)
        connection, _ = await _make_connection(db, user)
        agent = await _make_agent(db, user, connection=connection, name="Alpha")
        version = await _make_version(db, agent, model="claude-sonnet-4-6", strategy_text="Draft")
        await db.commit()

    cookies = _signed_in_cookies(user.id)
    draft_resp = await client.post(
        f"/me/agents/{agent.id}/set-strategy",
        cookies=cookies,
        data={"strategy_text": "Draft updated"},
        follow_redirects=False,
    )
    assert draft_resp.status_code == 303

    async with session_factory() as db:
        stored_version = (
            await db.execute(select(AgentVersion).where(AgentVersion.id == version.id))
        ).scalar_one()
        stored_agent = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        assert stored_version.strategy_text == "Draft updated"
        assert stored_agent.current_version_id == version.id

    async with session_factory() as db:
        user = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        agent = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        old_version = (await db.execute(select(AgentVersion).where(AgentVersion.id == version.id))).scalar_one()
        match = await _make_match(db, "M_1000", state=GameState.COMPLETED)
        await _seat_player(
            db,
            match=match,
            user=user,
            agent=agent,
            version=old_version,
            seat_name=f"{user.handle}/Alpha",
        )
        await db.commit()

    fork_resp = await client.post(
        f"/me/agents/{agent.id}/set-strategy",
        cookies=cookies,
        data={"strategy_text": "Forked strategy"},
        follow_redirects=False,
    )
    assert fork_resp.status_code == 303

    async with session_factory() as db:
        versions = (
            await db.execute(
                select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version_no)
            )
        ).scalars().all()
        assert len(versions) == 2
        assert versions[0].strategy_text == "Draft updated"
        assert versions[1].strategy_text == "Forked strategy"
        stored_agent = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        assert stored_agent.current_version_id == versions[1].id
        stored_player = (
            await db.execute(select(Player).where(Player.agent_version_id == versions[0].id))
        ).scalar_one()
        assert stored_player.agent_version_id == versions[0].id


@pytest.mark.asyncio
async def test_seat_name_uniqueness_allows_two_users_with_same_agent_name(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user_a = await _make_user(db, handle="alice", i=3)
        user_b = await _make_user(db, handle="bob", i=4)
        connection_a, _ = await _make_connection(db, user_a)
        connection_b, _ = await _make_connection(db, user_b)
        agent_a = await _make_agent(db, user_a, connection=connection_a, name="Alpha")
        agent_b = await _make_agent(db, user_b, connection=connection_b, name="Alpha")
        version_a = await _make_version(db, agent_a)
        version_b = await _make_version(db, agent_b)
        match = await _make_match(db, "M_2000", state=GameState.ACTIVE)
        await _seat_player(
            db,
            match=match,
            user=user_a,
            agent=agent_a,
            version=version_a,
            seat_name=f"{user_a.handle}/Alpha",
        )
        await _seat_player(
            db,
            match=match,
            user=user_b,
            agent=agent_b,
            version=version_b,
            seat_name=f"{user_b.handle}/Alpha",
        )
        await db.commit()

        seat_names = (
            await db.execute(select(Player.seat_name).where(Player.match_id == match.id))
        ).scalars().all()
        assert seat_names == ["alice/Alpha", "bob/Alpha"]


@pytest.mark.asyncio
async def test_agent_detail_shows_connection_capacity_when_at_limit(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """At-capacity card shows when the agent is connected-idle but join_blocked.

    When the agent IS already in the active match (the one that fills the slot),
    the onboarding card shows the match state instead of 'At capacity'. To see
    the capacity card, a second idle agent on the same connection must be used.
    The 'At capacity' card is part of the onboarding slot (connected_no_game +
    join_blocked=True) rather than a separate static card.
    """
    recently = datetime.now(timezone.utc) - timedelta(seconds=20)
    async with session_factory() as db:
        user = await _make_user(db, handle="agent5", i=5)
        connection, _ = await _make_connection(
            db,
            user,
            max_concurrent_games=1,
            provider=ConnectionProvider.CLAUDE,
        )
        # Set last_seen_at so the connection is warm (runner connected)
        connection.last_seen_at = recently
        connection.first_connected_at = recently
        await db.flush()
        agent = await _make_agent(db, user, connection=connection, name="Alpha")
        version = await _make_version(db, agent)
        # A second agent on the same connection — it's idle but the connection is full
        agent2 = await _make_agent(db, user, connection=connection, name="Beta")
        match = await _make_match(db, "M_3000", state=GameState.ACTIVE)
        await _seat_player(
            db,
            match=match,
            user=user,
            agent=agent,
            version=version,
            seat_name=f"{user.handle}/Alpha",
        )
        await db.commit()

    # agent2 is idle (connected_no_game) but the connection is at capacity
    resp = await client.get(
        f"/me/agents/{agent2.id}",
        cookies=_signed_in_cookies(user.id),
    )
    assert resp.status_code == 200
    assert "At capacity" in resp.text
    assert "1 / 1 active matches" in resp.text


@pytest.mark.asyncio
async def test_agent_in_active_practice_match_is_locked_against_delete_and_edit(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An agent seated in ANY active match — including a practice-arena game — must
    not be deletable or editable mid-match (CP2 review findings 1 & 2)."""
    async with session_factory() as db:
        user = await _make_user(db, handle="agent7", i=7)
        connection, _ = await _make_connection(
            db, user, provider=ConnectionProvider.CLAUDE
        )
        agent = await _make_agent(db, user, connection=connection, name="Locked")
        version = await _make_version(db, agent)
        match = await _make_match(
            db,
            "M_4000",
            state=GameState.ACTIVE,
            match_kind=MatchKind.PRACTICE_ARENA.value,
        )
        await _seat_player(
            db,
            match=match,
            user=user,
            agent=agent,
            version=version,
            seat_name=f"{user.handle}/Locked",
        )
        await db.commit()
        agent_id = agent.id

    cookies = _signed_in_cookies(user.id)
    delete_resp = await client.post(f"/me/agents/{agent_id}/delete", cookies=cookies)
    assert delete_resp.status_code == 409
    edit_resp = await client.post(
        f"/me/agents/{agent_id}/set-strategy",
        data={"strategy_text": "A different plan."},
        cookies=cookies,
    )
    assert edit_resp.status_code == 409
