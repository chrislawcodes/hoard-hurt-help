"""Fan-out tests for connection-scoped next-turn and agent binding."""

from __future__ import annotations

import asyncio
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
from app.engine.model_provider_match import default_model_for_provider
from app.engine.tokens import generate_turn_token
from app.models import Base
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
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
    # No preferred model set → payload carries the provider's default model
    # (the legacy AgentVersion.model is no longer forwarded).
    assert body["model"] == default_model_for_provider("claude")
    assert body["version_no"] == version.version_no
    assert body["seat_name"] == player.seat_name
    assert body["turn_token"] == body["current"]["turn_token"]
    assert body["agent_turn_token"] == f'{body["turn_token"]}:{agent.id}:M_0001'
    assert "rules" in body["static"]
    assert "base_prompt" in body["static"]
    assert f'as agent "{player.seat_name}"' in body["static"]["base_prompt"]
    assert "max 200 chars" in body["static"]["base_prompt"]
    assert "alpha strategy" not in body["static"]["base_prompt"]


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
    assert body["model"] == default_model_for_provider("claude")  # provider default
    assert body["version_no"] == version_b.version_no
    assert body["seat_name"] == player_b.seat_name
    assert body["match_id"] == match_b.id


async def test_same_match_agents_fetch_own_turn_and_wrong_agent_submit_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, turn = await _create_match_with_turn(db, "M_0200", deadline_seconds=60)
        agent_a, _version_a, _player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        agent_b, _version_b, _player_b = await _seat_agent(
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

    # Each agent fetching only its own turn is covered by the agent_id-filter
    # test; here the surviving contract is that a submit under the WRONG agent_id
    # for a claimed turn token is rejected without recording anything.
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


async def test_next_turn_agent_id_filter_and_batch_serve_each_agent(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two agents share one connection AND one match. agent_id fetches just one;
    the batch returns both; the no-arg fetch still serves the most-urgent."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_0201", deadline_seconds=60)
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

    # agent_id picks exactly that agent's turn — even though both share the match.
    only_a = await client.get(
        "/api/agent/next-turn",
        params={"agent_id": agent_a.id},
        headers={"X-Connection-Key": key},
    )
    assert only_a.status_code == 200, only_a.text
    body_a = only_a.json()
    assert body_a["status"] == "your_turn"
    assert body_a["agent_id"] == agent_a.id
    assert body_a["seat_name"] == player_a.seat_name

    only_b = await client.get(
        "/api/agent/next-turn",
        params={"agent_id": agent_b.id},
        headers={"X-Connection-Key": key},
    )
    assert only_b.status_code == 200, only_b.text
    body_b = only_b.json()
    assert body_b["status"] == "your_turn"
    assert body_b["agent_id"] == agent_b.id
    assert body_b["seat_name"] == player_b.seat_name

    # The batch returns BOTH agents' turns, one entry per agent.
    batch = await client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    batch_body = batch.json()
    assert batch_body["status"] == "your_turn"
    served_agent_ids = {turn["agent_id"] for turn in batch_body["turns"]}
    assert served_agent_ids == {agent_a.id, agent_b.id}

    # Regression: the no-arg fetch still serves a single most-urgent turn.
    any_turn = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert any_turn.status_code == 200, any_turn.text
    assert any_turn.json()["agent_id"] in {agent_a.id, agent_b.id}


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
    assert body["model"] == default_model_for_provider("claude")  # provider default
    assert body["version_no"] == early_version.version_no
    assert body["seat_name"] == early_player.seat_name


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


async def test_no_game_returns_no_game_immediately_with_idle_cadence(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A connection with NO game at all gets 'no_game' (not 'waiting') at once,
    carrying an idle count and the slow 5-minute idle cadence. The plural endpoint
    does the same. A freshly-connected caller is not told to stop yet."""
    async with session_factory() as db:
        user = await make_user(db)
        _connection, key = await make_connection(db, user)
        await db.commit()

    loop = asyncio.get_event_loop()
    started = loop.time()
    single = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    elapsed = loop.time() - started
    assert single.status_code == 200, single.text
    body = single.json()
    assert body["status"] == "no_game"
    # Returned immediately (no long-poll hold) and advised the slow idle cadence.
    assert elapsed < 0.5
    assert body["next_poll_after_seconds"] == 300
    # Just connected — idle clock barely started, so don't stop yet.
    assert body["should_stop"] is False
    assert body["idle_seconds"] < 60
    assert "stop_reason" not in body

    batch = await client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    bbody = batch.json()
    assert bbody["status"] == "no_game"
    assert bbody["next_poll_after_seconds"] == 300
    assert bbody["should_stop"] is False


async def test_long_poll_returns_waiting_after_window_when_seated_no_open_turn(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seated in an active game but with no open turn: a turn could open any
    moment, so the server long-polls — it holds the request open, then returns
    'waiting'. We shrink the server's hold so the test stays fast and assert the
    call actually spent close to the window before giving up."""
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Active match, agent seated, but NO open turn yet — a turn is still coming.
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_WAIT",
            name="match-M_WAIT",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
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

    loop = asyncio.get_event_loop()
    started = loop.time()
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    elapsed = loop.time() - started
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting"
    # It held roughly the whole window before returning (not an instant reply).
    assert elapsed >= 0.35
    # After a long-poll the client should re-open promptly (the hold was the wait).
    assert body["next_poll_after_seconds"] <= 5


async def test_long_poll_returns_promptly_when_a_turn_opens(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a turn opens partway through the hold, the long-poll returns it the
    moment its next re-check sees it — well before the full window elapses."""
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Seat the agent in an active match, but with NO open turn yet.
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_0800",
            name="match-M_0800",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        agent, _version, _player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        await db.commit()

    async def open_turn_soon() -> None:
        # Open the turn shortly after the long-poll begins holding.
        await asyncio.sleep(0.15)
        async with session_factory() as db:
            db.add(
                Turn(
                    match_id="M_0800",
                    round=1,
                    turn=1,
                    turn_token=generate_turn_token(),
                    opened_at=datetime.now(timezone.utc),
                    deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                    phase="act",
                )
            )
            await db.commit()

    loop = asyncio.get_event_loop()
    started = loop.time()
    # Long hold window, fast re-check interval: the response should come back when
    # the turn opens (~0.15s), not at the 5s window.
    opener = asyncio.create_task(open_turn_soon())
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    await opener
    elapsed = loop.time() - started
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["match_id"] == "M_0800"
    assert body["agent_id"] == agent.id
    # Returned promptly after the turn opened — nowhere near the full 5s window.
    assert elapsed < 2.0


async def test_pacing_is_agent_scoped_when_a_loop_asks_for_one_agent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A per-agent loop must pace off ITS own soonest game, not a busier sibling.

    Agent A's game is 25 min off; sibling agent B is live. Connection-wide, a live
    game means long-poll — but the loop scoped to A should see no live game and use
    the cheap 5-minute waiting cadence, so A's loop doesn't burn the fast in-play
    rate waiting on B's game."""
    from app.engine.agent_idle import compute_idle_status, pace_idle

    async with session_factory() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        far = Match(
            id="M_FAR",
            name="match-M_FAR",
            state=GameState.SCHEDULED,
            scheduled_start=now + timedelta(minutes=25),
            per_turn_deadline_seconds=60,
            current_round=0,
            current_turn=0,
        )
        live = Match(
            id="M_LIVE",
            name="match-M_LIVE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add_all([far, live])
        await db.flush()
        agent_a, _va, _pa = await _seat_agent(
            db, user=user, connection=connection, match=far,
            seat_name=f"{user.handle}/A", agent_name="A",
            model="claude-sonnet-4-6", strategy_text="s",
        )
        await _seat_agent(
            db, user=user, connection=connection, match=live,
            seat_name=f"{user.handle}/B", agent_name="B",
            model="claude-sonnet-4-6", strategy_text="s",
        )
        await db.commit()
        connection_id = connection.id
        agent_a_id = agent_a.id

    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        # Connection-wide: B is live → long-poll.
        whole = await compute_idle_status(db, conn)
        assert whole.has_live_game is True
        assert pace_idle(whole)[0] > 0  # holds the line open

        # Scoped to agent A: its only game is 25 min off → no hold, 5-min cadence.
        scoped = await compute_idle_status(db, conn, agent_id=agent_a_id)
        assert scoped.has_live_game is False
        assert scoped.seconds_to_next_start is not None
        assert scoped.seconds_to_next_start > 600
        hold, next_poll = pace_idle(scoped)
        assert hold == 0.0
        assert next_poll == 300


async def test_api_call_count_increments_and_turn_count_on_real_submit(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Every authenticated call bumps api_call_count; a real (non-defaulted)
    submit bumps turns_played. The detail page reads these raw counts."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, turn = await _create_match_with_turn(db, "M_0900", deadline_seconds=60)
        agent, _version, _player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        await db.commit()
        connection_id = connection.id

    # One poll that serves a turn.
    served = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["status"] == "your_turn"
    agent_turn_token = body["agent_turn_token"]
    turn_token = body["turn_token"]

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        assert stored.api_call_count == 1
        assert stored.turns_played == 0

    submit = await client.post(
        f"/api/matches/M_0900/submit?agent_turn_token={agent_turn_token}",
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn_token,
            "action": "HOARD",
            "target_id": None,
            "message": "mine",
            "thinking": "",
        },
    )
    assert submit.status_code == 202, submit.text

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        # The submit was one more authenticated call (count now 2) and one real
        # turn played.
        assert stored.api_call_count == 2
        assert stored.turns_played == 1


async def test_no_game_after_idle_window_tells_client_to_stop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A connection with no game that has been idle past the ~10-min window gets
    should_stop=True with a stop_reason, so an interactive client stops polling."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Back-date every idle anchor well past the 10-minute window.
        long_ago = datetime.now(timezone.utc) - timedelta(minutes=20)
        connection.first_connected_at = long_ago
        connection.mcp_connected_at = long_ago
        connection.created_at = long_ago
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_game"
    assert body["should_stop"] is True
    assert body["stop_reason"] == "idle_timeout"
    assert body["idle_seconds"] >= 600


async def test_seated_in_active_game_is_waiting_not_no_game(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even long after the idle window, a caller seated in an active game (turn not
    open) is 'waiting' — a turn is coming — and is never told to stop."""
    # Shrink the server's long-poll hold so the test skips the full production
    # wait; the assertions below (waiting, no stop hint) are unchanged.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        connection.first_connected_at = datetime.now(timezone.utc) - timedelta(hours=2)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_SEATED",
            name="match-M_SEATED",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
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

    # No open turn yet -> waiting (not no_game), and no stop hint.
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting"
    assert "should_stop" not in body


async def test_scheduled_game_keeps_caller_waiting_not_no_game(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A caller seated in a not-yet-started (scheduled) game is 'waiting' — the
    game is about to start, so never 'no_game' and never told to stop."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        connection.first_connected_at = datetime.now(timezone.utc) - timedelta(hours=2)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_SCHED",
            name="match-M_SCHED",
            state=GameState.SCHEDULED,
            scheduled_start=now + timedelta(minutes=5),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
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
    body = r.json()
    assert body["status"] == "waiting"
    assert "should_stop" not in body


async def test_provider_agnostic_serving_stamps_played_provider(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An agent with no provider is served by ANY of the user's live connections,
    and the serving connection's provider is stamped onto the player as
    played_provider (the source of truth for the public 'played by' badge)."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(
            db, user, provider=ConnectionProvider.GEMINI
        )
        match, _turn = await _create_match_with_turn(db, "M_PA01", deadline_seconds=60)
        agent, version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Decoupled",
            agent_name="Decoupled",
            model="claude-sonnet-4-6",
            strategy_text="s",
        )
        # Decoupled agent: no stored provider, no stored model.
        agent.provider = None
        version.model = None
        await db.commit()
        player_id = player.id

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["agent_name"] == "Decoupled"
    # Payload provider reflects the serving connection, not the (absent) agent provider.
    assert body["provider"] == "gemini"
    # Decoupled agent (no preferred model) → the serving provider's default model.
    assert body["model"] == default_model_for_provider("gemini")

    async with session_factory() as db:
        refreshed = (
            await db.execute(select(Player).where(Player.id == player_id))
        ).scalar_one()
        assert refreshed.played_provider == "gemini"
        assert refreshed.served_by_connection_id == connection.id


async def test_connection_only_serves_seats_for_its_own_ai(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matched routing: a seat joined with one AI is served only to a connection
    that covers that AI. A different-provider connection of the same user is
    handed nothing — so the seat plays as the AI the user picked, not whoever
    polls first."""
    # The non-matching connection has nothing to serve, so it long-polls; shrink
    # the hold so the test skips the full production wait. Routing assertions are
    # unchanged.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        _claude_conn, claude_key = await make_connection(
            db, user, provider=ConnectionProvider.CLAUDE
        )
        gemini_conn, gemini_key = await make_connection(
            db, user, provider=ConnectionProvider.GEMINI
        )
        match, _turn = await _create_match_with_turn(db, "M_MATCH", deadline_seconds=60)
        # Seat is joined with the Gemini connection → chosen_provider = "gemini".
        await _seat_agent(
            db,
            user=user,
            connection=gemini_conn,
            match=match,
            seat_name=f"{user.handle}/Gem",
            agent_name="Gem",
            model="gemini-3.1-flash-lite",
            strategy_text="s",
        )
        await db.commit()

    # The Claude connection covers only "claude" → it is NOT handed the gemini seat.
    r_claude = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": claude_key})
    assert r_claude.status_code == 200, r_claude.text
    assert r_claude.json()["status"] != "your_turn"

    # The Gemini connection covers "gemini" → it gets the turn, as Gemini.
    r_gemini = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": gemini_key})
    assert r_gemini.status_code == 200, r_gemini.text
    assert r_gemini.json()["status"] == "your_turn"
    assert r_gemini.json()["provider"] == "gemini"


async def test_next_turn_history_is_windowed_to_recent_turns(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The next-turn payload (the connector + MCP path) carries only the last
    couple of resolved turns, not the whole transcript — so a long mid-game match
    can't overflow a client's tool-output buffer and trip its loop detection."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_WIN",
            name="match-M_WIN",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=4,
        )
        db.add(match)
        await db.flush()
        _agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="s",
        )
        # Three resolved turns (1,1)..(1,3), then the open turn (1,4) the poll serves.
        for t in (1, 2, 3):
            resolved = Turn(
                match_id=match.id,
                round=1,
                turn=t,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now,
                resolved_at=now,
                phase="act",
            )
            db.add(resolved)
            await db.flush()
            db.add(
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player.id,
                    action="HOARD",
                    target_player_id=None,
                    message=f"m{t}",
                    points_delta=2,
                    was_defaulted=False,
                )
            )
        db.add(
            Turn(
                match_id=match.id,
                round=1,
                turn=4,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
                phase="act",
            )
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    # Only the last two resolved turns ride along — (1,1) is dropped from the poll.
    assert [(t["round"], t["turn"]) for t in body["history"]] == [(1, 2), (1, 3)]


async def test_next_turn_payload_includes_current_pact_values(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`your_private_state.pact_values` carries what a mutual HELP with each
    other seat would pay each side RIGHT NOW: decayed for a partner the agent
    already farmed once this match, fresh for one it never mutually helped
    (routed through `module.private_state_for`)."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_PACT",
            name="match-M_PACT",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=2,
        )
        db.add(match)
        await db.flush()
        agent_a, _version_a, player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="s",
        )
        _agent_b, _version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="s",
        )
        _agent_c, _version_c, player_c = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Gamma",
            agent_name="Gamma",
            model="claude-opus-4-1",
            strategy_text="s",
        )
        # Round 1, turn 1 (resolved): Alpha <-> Beta mutually helped once, so
        # their pair's k is now 1; Gamma stayed out of it (fresh pair with Alpha).
        resolved = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now,
            resolved_at=now,
            phase="act",
        )
        db.add(resolved)
        await db.flush()
        db.add_all(
            [
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player_a.id,
                    action="HELP",
                    target_player_id=player_b.id,
                    was_defaulted=False,
                ),
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player_b.id,
                    action="HELP",
                    target_player_id=player_a.id,
                    was_defaulted=False,
                ),
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player_c.id,
                    action="HOARD",
                    target_player_id=None,
                    was_defaulted=False,
                ),
            ]
        )
        # Round 1, turn 2: the open turn served next.
        db.add(
            Turn(
                match_id=match.id,
                round=1,
                turn=2,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
                phase="act",
            )
        )
        await db.commit()

    # Next-turn fan-out path.
    r = await client.get(
        "/api/agent/next-turn",
        params={"agent_id": agent_a.id},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    pact_values = body["your_private_state"]["pact_values"]
    assert pact_values[player_b.seat_name] == 7  # farmed once already: 8 decays to 7
    assert pact_values[player_c.seat_name] == 8  # never mutually helped: fresh value
    assert "pact_values_note" in body["your_private_state"]


async def test_coach_note_served_on_turn_payload(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A coach note armed for the CURRENT round rides on the turn payload,
    gated to that round."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_COACH", deadline_seconds=60)
        agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        player.coach_note = "Be cooperative this round"
        player.coach_note_round = match.current_round  # active NOW
        await db.commit()

    fanout = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert fanout.status_code == 200, fanout.text
    fanout_body = fanout.json()
    assert fanout_body["status"] == "your_turn"
    assert fanout_body["static"]["coach_note"] == "Be cooperative this round"


async def test_coach_note_for_a_future_round_is_absent_from_turn_payload(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The round gating holds: a note armed for a LATER round does not appear in
    the payload's static block."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_COACH2", deadline_seconds=60)
        agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        player.coach_note = "Armed for a later round"
        player.coach_note_round = match.current_round + 1
        await db.commit()

    fanout = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert fanout.status_code == 200, fanout.text
    assert "coach_note" not in fanout.json()["static"]


async def test_turn_static_block_carries_unified_fields(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The next-turn static block carries the full identity/rules field set built
    by build_turn_static_dict — including the conditional coach_note — not an
    empty shell."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_DRIFT", deadline_seconds=60)
        agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            strategy_text="alpha strategy",
        )
        player.coach_note = "Watch the leader"
        player.coach_note_round = match.current_round
        await db.commit()

    fanout = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert fanout.status_code == 200, fanout.text

    static = fanout.json()["static"]
    for field in ("match_id", "game_id", "game", "rules", "base_prompt", "coach_note"):
        assert field in static


async def test_filter_to_candidates_batches_mixed_phase_seats(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The batched owes-a-move filter behaves per seat exactly like the old
    per-seat queries across a mixed board: an act turn already submitted is
    skipped, a talk turn already messaged is skipped, a talk turn not yet
    messaged is served, a seat whose only submission was defaulted is served,
    and a seat with no open turn at all serves nothing."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # A: act phase, real submission -> skipped.
        match_a, turn_a = await _create_match_with_turn(db, "M_FA", deadline_seconds=60)
        # B: talk phase, real talk message -> skipped.
        match_b, turn_b = await _create_match_with_turn(
            db, "M_FB", deadline_seconds=60, phase="talk"
        )
        # C: talk phase, no message yet -> served.
        match_c, _turn_c = await _create_match_with_turn(
            db, "M_FC", deadline_seconds=60, phase="talk"
        )
        # D: act phase, only a DEFAULTED submission -> still owed, served.
        match_d, turn_d = await _create_match_with_turn(db, "M_FD", deadline_seconds=60)
        # E: active match with NO open turn -> nothing to serve.
        now = datetime.now(timezone.utc)
        match_e = Match(
            id="M_FE",
            name="match-M_FE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match_e)
        await db.flush()

        players = {}
        for label, match in (
            ("A", match_a),
            ("B", match_b),
            ("C", match_c),
            ("D", match_d),
            ("E", match_e),
        ):
            _agent, _version, player = await _seat_agent(
                db,
                user=user,
                connection=connection,
                match=match,
                seat_name=f"{user.handle}/{label}",
                agent_name=label,
                model="claude-sonnet-4-6",
                strategy_text="s",
            )
            players[label] = player
        db.add(
            TurnSubmission(
                turn_id=turn_a.id,
                player_id=players["A"].id,
                action="HOARD",
                target_player_id=None,
                was_defaulted=False,
            )
        )
        db.add(
            TurnMessage(
                turn_id=turn_b.id,
                player_id=players["B"].id,
                text="already talked",
                was_defaulted=False,
                submitted_at=now,
            )
        )
        db.add(
            TurnSubmission(
                turn_id=turn_d.id,
                player_id=players["D"].id,
                action="HOARD",
                target_player_id=None,
                was_defaulted=True,
            )
        )
        await db.commit()

    batch = await client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert body["status"] == "your_turn"
    assert sorted(t["match_id"] for t in body["turns"]) == ["M_FC", "M_FD"]
