"""Agent soft-delete: one Delete action that does the right thing.

A user AI agent with no game history is hard-deleted (its row is gone). A user
AI agent that has ever been in a game is soft-deleted instead — archived and
paused, its row kept so the players that FK back to it keep their history. An
archived agent is hidden from the owner's lists, rejected from new games, and
its key stops working.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.sim_presets import sim_presets
from app.main import app
from app.models import Base, Agent, AgentKind, AgentStatus, Connection, Match, GameState, Player, User
from tests.factories import make_agent, make_connection, make_user


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

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _signed_in_cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_game(
    reset_db: async_sessionmaker, state=GameState.REGISTERING, match_id: str = "G_001"
) -> Match:
    async with reset_db() as db:
        g = Match(
            id=match_id,
            name="Test Match",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


async def _seed_agent(
    reset_db: async_sessionmaker, user: User, name: str = "Atlas"
) -> tuple[Agent, str, Connection]:
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, key = await make_connection(db, u)
        agent, _ = await make_agent(db, u, connection=connection, name=name)
        connection.first_connected_at = datetime.now(timezone.utc)
        connection.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        return agent, key, connection


async def _give_history(reset_db: async_sessionmaker, user: User, agent_id: int) -> None:
    """Seat the agent as a player in a game so it has game history."""
    await _seed_game(reset_db)
    async with reset_db() as db:
        db.add(
            Player(
                match_id="G_001",
                user_id=user.id,
                agent_id=agent_id,
                seat_name="atlas",
            )
        )
        await db.commit()


async def _get_agent(reset_db: async_sessionmaker, agent_id: int) -> Agent | None:
    async with reset_db() as db:
        return (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_delete_without_history_hard_deletes(client, reset_db):
    """An agent that never played is removed entirely."""
    user = await _seed_user(reset_db)
    agent, _key, _connection = await _seed_agent(reset_db, user)

    r = await client.post(
        f"/me/agents/{agent.id}/delete",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/me/agents"
    assert await _get_agent(reset_db, agent.id) is None


@pytest.mark.asyncio
async def test_delete_with_history_archives_instead(client, reset_db):
    """An agent with game history is archived + paused, not removed."""
    user = await _seed_user(reset_db)
    agent, _key, connection = await _seed_agent(reset_db, user)
    await _give_history(reset_db, user, agent.id)

    r = await client.post(
        f"/me/agents/{agent.id}/delete",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    archived = await _get_agent(reset_db, agent.id)
    assert archived is not None, "agent with history must be kept, not deleted"
    assert archived.archived_at is not None
    assert archived.status == AgentStatus.PAUSED
    assert archived.connection_id is None
    assert connection.id is not None


@pytest.mark.asyncio
async def test_archived_bot_hidden_from_my_bots(client, reset_db):
    """An archived agent no longer appears in the owner's agent list."""
    user = await _seed_user(reset_db)
    agent, _key, _connection = await _seed_agent(reset_db, user, name="Ghost")
    await _give_history(reset_db, user, agent.id)
    await client.post(
        f"/me/agents/{agent.id}/delete", cookies=_signed_in_cookies(user.id)
    )

    r = await client.get("/me/agents", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Ghost" not in r.text


@pytest.mark.asyncio
async def test_archived_bot_key_stops_authenticating(client, reset_db):
    """Once archived, the agent's key is rejected like an unknown key."""
    user = await _seed_user(reset_db)
    agent, key, _connection = await _seed_agent(reset_db, user)
    await _give_history(reset_db, user, agent.id)  # seats the agent in G_001

    # Sanity: the key works before deletion.
    ok = await client.get("/api/games/G_001/turn", headers={"X-Connection-Key": key})
    assert ok.status_code != 401

    await client.post(
        f"/me/agents/{agent.id}/delete", cookies=_signed_in_cookies(user.id)
    )

    r = await client.get("/api/games/G_001/turn", headers={"X-Connection-Key": key})
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NOT_IN_GAME"


@pytest.mark.asyncio
async def test_archived_bot_cannot_join_new_game(client, reset_db):
    """A crafted join POST naming an archived bot is rejected."""
    user = await _seed_user(reset_db)
    agent, _key, _connection = await _seed_agent(reset_db, user)
    await _give_history(reset_db, user, agent.id)
    await client.post(
        f"/me/agents/{agent.id}/delete", cookies=_signed_in_cookies(user.id)
    )
    await _seed_game(reset_db, state=GameState.REGISTERING, match_id="G_002")

    r = await client.post(
        "/games/hoard-hurt-help/matches/G_002/join",
        data={"agent_id": agent.id, "display_name": "atlas2", "strategy_prompt": ""},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_archived_agent_keeps_its_name_and_can_be_replaced(client, reset_db):
    """Archived agents keep their stored name, and a new distinct agent can still be created."""
    user = await _seed_user(reset_db)
    agent, _key, _connection = await _seed_agent(reset_db, user, name="Atlas")
    await _give_history(reset_db, user, agent.id)
    await client.post(
        f"/me/agents/{agent.id}/delete", cookies=_signed_in_cookies(user.id)
    )

    archived = await _get_agent(reset_db, agent.id)
    assert archived is not None
    assert archived.name == "Atlas"

    # A separate agent can still be created for the user after archival.
    async with reset_db() as db:
        connection, _ = await make_connection(db, user)
        agent2, _ = await make_agent(db, user, connection=connection, name="Atlas 2")
        await db.commit()
    assert agent2.name == "Atlas 2"


@pytest.mark.asyncio
async def test_archived_name_fits_120_char_column(client, reset_db):
    """A max-length (120-char) name still fits after archive."""
    user = await _seed_user(reset_db)
    long_name = "B" * 120
    agent, _key, _connection = await _seed_agent(reset_db, user, name=long_name)
    await _give_history(reset_db, user, agent.id)
    await client.post(
        f"/me/agents/{agent.id}/delete", cookies=_signed_in_cookies(user.id)
    )

    archived = await _get_agent(reset_db, agent.id)
    assert archived is not None
    assert len(archived.name) <= 120
    assert archived.name == long_name


@pytest.mark.asyncio
async def test_archived_agent_keeps_profile_metadata(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)

    presets = sim_presets()
    async with reset_db() as db:
        connection, _ = await make_connection(db, user)
        db.add(
            Match(
                id="G_001",
                name="Test Match",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                per_turn_deadline_seconds=60,
            )
        )
        agent = Agent(
            user_id=user.id,
            connection_id=connection.id,
            kind=AgentKind.AI,
            name="PresetSim",
            game="hoard-hurt-help",
            status=AgentStatus.ACTIVE,
            bot_profile_id=presets[0].id,
            bot_profile_name=presets[0].name,
            bot_strategy=presets[0].strategy,
            bot_truthfulness=presets[0].truthfulness,
            bot_trust_model=presets[0].trust_model,
            bot_seed=42,
            bot_version="v1",
        )
        db.add(agent)
        await db.flush()
        db.add(
            Player(
                match_id="G_001",
                user_id=user.id,
                agent_id=agent.id,
                seat_name="AI_SIM",
            )
        )
        await db.commit()
    r = await client.post(
        f"/me/agents/{agent.id}/delete",
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        archived = (
            await db.execute(select(Agent).where(Agent.id == agent.id))
        ).scalar_one()
    assert archived.bot_profile_id == presets[0].id
    assert archived.bot_profile_name == presets[0].name
    assert archived.bot_strategy == presets[0].strategy
