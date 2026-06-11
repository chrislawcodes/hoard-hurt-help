"""Fan-out tests for connection-scoped next-turn and agent binding."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.engine.tokens import generate_turn_token
from app.models import Base
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.routes.agent_api import router as agent_api_router
from app.routes.agent_next_turn import router as agent_next_turn_router


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
    monkeypatch.setattr("app.routes.agent_api._last_poll", {})
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})
    test_app = FastAPI()
    test_app.include_router(agent_api_router, prefix="/api/matches/{match_id}")
    test_app.include_router(agent_api_router, prefix="/api/games/{match_id}")
    test_app.include_router(agent_next_turn_router)
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_user(db: AsyncSession, i: int = 0) -> User:
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@t.com",
        handle=f"agent{i}",
        handle_key=f"agent{i}",
    )
    db.add(user)
    await db.flush()
    return user


async def make_connection(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    nickname: str | None = None,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    key: str | None = None,
) -> tuple[Connection, str]:
    plain_key = key or f"sk_conn_{secrets.token_hex(24)}"
    connection = Connection(
        user_id=user.id,
        nickname=nickname,
        provider=provider,
        key_lookup=hashlib.sha256(plain_key.encode("utf-8")).hexdigest(),
        key_hint=plain_key[-4:],
        status=status,
    )
    db.add(connection)
    await db.flush()
    db.add(
        ConnectionProviderRow(
            connection_id=connection.id,
            provider=provider,
            enabled=True,
            detected=False,
        )
    )
    await db.flush()
    return connection, plain_key


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
        phase="act",
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
        model_self_report=model,
    )
    db.add(player)
    await db.flush()
    return agent, version, player


@pytest.mark.asyncio
async def test_one_connection_one_agent_one_match_returns_correct_version(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_0001", deadline_seconds=60)
        agent, version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/{'Alpha'}",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["match_id"] == "M_0001"
    assert body["agent_id"] == agent.id
    assert body["agent_name"] == "Alpha"
    assert body["model"] == version.model
    assert body["version_no"] == version.version_no
    assert body["seat_name"] == player.seat_name
    assert body["turn_token"] == body["current"]["turn_token"]
    assert body["agent_turn_token"] == f'{body["turn_token"]}:{agent.id}:M_0001'


@pytest.mark.asyncio
async def test_multiple_agents_and_matches_pick_the_most_urgent_turn(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match_a, _turn_a = await _create_match_with_turn(db, "M_0100", deadline_seconds=120)
        match_b, _turn_b = await _create_match_with_turn(db, "M_0101", deadline_seconds=30)
        _agent_a, _version_a, _player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_a,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-haiku-4-5",
            strategy_text="alpha strategy",
        )
        agent_b, version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_b,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-opus-4-1",
            strategy_text="beta strategy",
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == agent_b.id
    assert body["agent_name"] == "Beta"
    assert body["model"] == version_b.model
    assert body["version_no"] == version_b.version_no
    assert body["seat_name"] == player_b.seat_name
    assert body["match_id"] == match_b.id


@pytest.mark.asyncio
async def test_same_match_agents_fetch_own_turn_and_wrong_agent_submit_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, turn = await _create_match_with_turn(db, "M_0200", deadline_seconds=60)
        agent_a, _version_a, player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        agent_b, _version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        await db.commit()

    poll_a = await client.get(
        f"/api/matches/{match.id}/turn",
        params={"agent_id": agent_a.id},
        headers={"X-Connection-Key": key},
    )
    assert poll_a.status_code == 200, poll_a.text
    body_a = poll_a.json()
    assert body_a["status"] == "your_turn"
    assert body_a["static"]["your_agent_id"] == player_a.seat_name

    poll_b = await client.get(
        f"/api/matches/{match.id}/turn",
        params={"agent_id": agent_b.id},
        headers={"X-Connection-Key": key},
    )
    assert poll_b.status_code == 200, poll_b.text
    body_b = poll_b.json()
    assert body_b["status"] == "your_turn"
    assert body_b["static"]["your_agent_id"] == player_b.seat_name

    next_turn = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert next_turn.status_code == 200, next_turn.text
    next_body = next_turn.json()

    wrong_agent_id = agent_b.id if next_body["agent_id"] == agent_a.id else agent_a.id
    wrong_submit = await client.post(
        f"/api/matches/{match.id}/submit",
        params={
            "agent_turn_token": next_body["agent_turn_token"],
            "agent_id": wrong_agent_id,
        },
        headers={"X-Connection-Key": key},
        json={
            "turn_token": next_body["turn_token"],
            "action": "HOARD",
            "target_id": None,
            "message": "hi",
            "thinking": "",
        },
    )
    assert wrong_submit.status_code == 409
    assert wrong_submit.json()["detail"]["error"]["code"] == "STALE_TURN_TOKEN"

    async with session_factory() as db:
        submissions = (
            await db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        ).scalars().all()
        assert submissions == []

    correct_submit = await client.post(
        f"/api/matches/{match.id}/submit",
        params={
            "agent_turn_token": next_body["agent_turn_token"],
            "agent_id": next_body["agent_id"],
        },
        headers={"X-Connection-Key": key},
        json={
            "turn_token": next_body["turn_token"],
            "action": "HOARD",
            "target_id": None,
            "message": "hi",
            "thinking": "",
        },
    )
    assert correct_submit.status_code == 202, correct_submit.text


@pytest.mark.asyncio
async def test_paused_connection_next_turn_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        _connection, key = await make_connection(db, user, status=ConnectionStatus.PAUSED)
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "CONNECTION_PAUSED"


@pytest.mark.asyncio
async def test_urgency_ordering_prefers_the_earliest_deadline(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        late_match, _ = await _create_match_with_turn(db, "M_0300", deadline_seconds=90)
        early_match, _ = await _create_match_with_turn(db, "M_0301", deadline_seconds=15)
        _late_agent, _late_version, _late_player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=late_match,
            seat_name=f"{user.handle}/Late",
            agent_name="Late",
            model="claude-haiku-4-5",
            strategy_text="late strategy",
        )
        early_agent, early_version, early_player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=early_match,
            seat_name=f"{user.handle}/Early",
            agent_name="Early",
            model="claude-opus-4-1",
            strategy_text="early strategy",
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == early_agent.id
    assert body["match_id"] == early_match.id
    assert body["model"] == early_version.model
    assert body["version_no"] == early_version.version_no
    assert body["seat_name"] == early_player.seat_name


@pytest.mark.asyncio
async def test_next_turn_payload_includes_provider(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        match, _turn = await _create_match_with_turn(db, "M_PROV", deadline_seconds=60)
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="s",
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "claude"


@pytest.mark.asyncio
async def test_report_pid_with_detected_providers_sets_detected_only(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()
        conn_id = connection.id

    r = await client.post(
        "/api/agent/report-pid",
        json={"pid": 4321, "detected_providers": ["claude", "openai"]},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 204, r.text

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id == conn_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_provider = {row.provider.value: row for row in rows}
        # claude was the legacy enabled row: detected flips True, enabled untouched
        assert by_provider["claude"].detected is True
        assert by_provider["claude"].enabled is True
        # openai newly detected: detected True, enabled stays False (toggle is sacred)
        assert by_provider["openai"].detected is True
        assert by_provider["openai"].enabled is False
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.runner_pid == 4321


@pytest.mark.asyncio
async def test_report_pid_without_detected_providers_still_works(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An OLD connector posts only {pid: ...}; it must not error (acceptance #7)."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        await db.commit()
        conn_id = connection.id

    r = await client.post(
        "/api/agent/report-pid", json={"pid": 99}, headers={"X-Connection-Key": key}
    )
    assert r.status_code == 204, r.text
    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.runner_pid == 99


@pytest.mark.asyncio
async def test_report_pid_hostname_defaults_unnamed_connection(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An unnamed machine takes the reported hostname as its default name."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, nickname=None)
        await db.commit()
        conn_id = connection.id

    r = await client.post(
        "/api/agent/report-pid",
        json={"pid": 7, "hostname": "chris-macbook"},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 204, r.text
    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.nickname == "chris-macbook"


@pytest.mark.asyncio
async def test_report_pid_hostname_never_overrides_a_typed_name(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A name the operator typed always wins over the hostname default."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, nickname="Battlestation")
        await db.commit()
        conn_id = connection.id

    r = await client.post(
        "/api/agent/report-pid",
        json={"pid": 7, "hostname": "chris-macbook"},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 204, r.text
    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.nickname == "Battlestation"


@pytest.mark.asyncio
async def test_failover_live_connection_serves_match_pinned_to_dead_connection(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        # Dead connection: stale last_seen, holds the pin.
        dead, _dead_key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        dead.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        # Live connection covering the same provider.
        live, live_key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        live.last_seen_at = datetime.now(timezone.utc)
        match, _turn = await _create_match_with_turn(db, "M_FAIL", deadline_seconds=60)
        _agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=dead,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="s",
        )
        # Pin the match to the now-dead connection.
        player.served_by_connection_id = dead.id
        player.served_pinned_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        await db.commit()
        live_id = live.id
        player_id = player.id

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": live_key})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "your_turn"
    # The pin moved to the live connection (failover).
    async with session_factory() as db:
        moved = (
            await db.execute(select(Player).where(Player.id == player_id))
        ).scalar_one()
        assert moved.served_by_connection_id == live_id


@pytest.mark.asyncio
async def test_next_turns_returns_every_servable_turn_at_once(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The batch endpoint hands back ALL open turns across the connection's
    matches in one poll, so the runner can drive them concurrently. The singular
    endpoint, by contrast, returns only the most urgent one.
    """
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match_a, _turn_a = await _create_match_with_turn(db, "M_0701", deadline_seconds=60)
        match_b, _turn_b = await _create_match_with_turn(db, "M_0702", deadline_seconds=30)
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_a,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_b,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        await db.commit()

    # Singular endpoint: only the most urgent (M_0702, nearer deadline).
    single = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert single.status_code == 200, single.text
    assert single.json()["match_id"] == "M_0702"

    # Batch endpoint: BOTH matches in one response.
    batch = await client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert body["status"] == "your_turn"
    match_ids = sorted(t["match_id"] for t in body["turns"])
    assert match_ids == ["M_0701", "M_0702"]
    # Each turn carries its own binding token so workers submit independently.
    assert all(t["agent_turn_token"] for t in body["turns"])
    assert len({t["agent_turn_token"] for t in body["turns"]}) == 2


@pytest.mark.asyncio
async def test_next_turns_omits_a_turn_already_submitted(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A turn the agent has already moved on drops out of the batch, so a worker
    isn't re-dispatched for work that's done."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match_a, _turn_a = await _create_match_with_turn(db, "M_0711", deadline_seconds=60)
        match_b, turn_b = await _create_match_with_turn(db, "M_0712", deadline_seconds=60)
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_a,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        _agent_b, _version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_b,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        # Beta has already submitted a real (non-defaulted) move for its turn.
        db.add(
            TurnSubmission(
                turn_id=turn_b.id,
                player_id=player_b.id,
                action="HOARD",
                target_player_id=None,
                was_defaulted=False,
            )
        )
        await db.commit()

    batch = await client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert [t["match_id"] for t in body["turns"]] == ["M_0711"]
