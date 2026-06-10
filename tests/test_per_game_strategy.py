"""Per-game strategy at entry (preset or free text); the profile library is gone."""

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.games import get as get_game_module
from app.main import app
from app.models import Base, Agent
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider, ConnectionStatus
from tests.factories import make_connection, make_user


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


async def _seed_game_user_agent(
    reset_db: async_sessionmaker,
) -> tuple[int, int]:
    """Create a signed-in user with one active connection."""
    async with reset_db() as db:
        user = await make_user(db)
        await db.flush()
        connection, _ = await make_connection(db, user)
        connection.first_connected_at = datetime.now(timezone.utc)
        connection.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        return user.id, connection.id


async def _latest_strategy(reset_db: async_sessionmaker, agent_id: int) -> str:
    async with reset_db() as db:
        prompt = (
            await db.execute(
                select(AgentVersion.strategy_text)
                .where(AgentVersion.agent_id == agent_id)
                .order_by(AgentVersion.version_no.desc())
            )
        ).scalar_one()
        return prompt


def test_pd_module_exposes_presets_and_default() -> None:
    module = get_game_module("hoard-hurt-help")
    presets = module.strategy_presets()
    assert len(presets) >= 1
    for p in presets:
        assert p.id and p.name and p.prompt
    assert module.default_strategy().strip()


@pytest.mark.asyncio
async def test_join_with_custom_strategy_seeds_it(client, reset_db) -> None:
    user_id, connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "connection_id": connection_id,
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "CUSTOM: always cooperate.",
        },
        cookies=_signed_in_cookies(user_id),
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user_id, Agent.name == "Atlas")
            )
        ).scalar_one()
    assert await _latest_strategy(reset_db, agent_id) == "CUSTOM: always cooperate."


@pytest.mark.asyncio
async def test_join_without_strategy_uses_module_default(client, reset_db) -> None:
    user_id, connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "connection_id": connection_id,
            "name": "Atlas",
            "model": "claude-haiku-4-5",
        },
        cookies=_signed_in_cookies(user_id),
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user_id, Agent.name == "Atlas")
            )
        ).scalar_one()
    seeded = await _latest_strategy(reset_db, agent_id)
    assert seeded == get_game_module("hoard-hurt-help").default_strategy()


@pytest.mark.asyncio
async def test_join_with_preset_strategy_seeds_preset_prompt(client, reset_db) -> None:
    user_id, connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "connection_id": connection_id,
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_preset": "tit_for_tat",
        },
        cookies=_signed_in_cookies(user_id),
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user_id, Agent.name == "Atlas")
            )
        ).scalar_one()
    expected = next(
        preset.prompt
        for preset in get_game_module("hoard-hurt-help").strategy_presets()
        if preset.id == "tit_for_tat"
    )
    assert await _latest_strategy(reset_db, agent_id) == expected


@pytest.mark.asyncio
async def test_join_form_only_lists_connected_providers_and_uses_chips(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await make_connection(
            db,
            user,
            provider=ConnectionProvider.OPENAI,
            status=ConnectionStatus.PAUSED,
        )
        await db.commit()

    r = await client.get(
        "/me/agents/new?provider=openai",
        cookies=_signed_in_cookies(user.id),
    )
    assert r.status_code == 200
    # Only connected providers should appear.
    assert 'name="provider"' in r.text
    assert 'value="claude"' in r.text
    assert 'value="openai"' not in r.text
    assert 'name="model"' in r.text
    assert 'name="strategy_preset"' not in r.text
    assert 'name="strategy_text"' in r.text
    assert "Using your newest active" not in r.text
    assert 'data-preset-id="tit_for_tat"' in r.text
    assert 'data-preset-id="custom"' in r.text
    assert r.text.index('data-preset-id="tit_for_tat"') < r.text.index('data-preset-id="custom"')
    tit_snippet = r.text[r.text.index('data-preset-id="tit_for_tat"') : r.text.index('data-preset-id="tit_for_tat"') + 220]
    assert 'aria-pressed="true"' in tit_snippet


@pytest.mark.asyncio
async def test_join_page_without_active_connection_points_to_connections(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    r = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "No connection yet" in r.text
    assert 'href="/me/connections"' in r.text
    assert 'name="strategy_preset"' not in r.text


@pytest.mark.asyncio
async def test_join_with_disconnected_provider_is_rejected(client, reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()

    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "strategy_text": "CUSTOM: always cooperate.",
        },
        cookies=_signed_in_cookies(user.id),
    )
    assert r.status_code == 409, r.text
    assert "No connection runs openai" in r.text


@pytest.mark.asyncio
async def test_strategy_profiles_route_removed(client, reset_db) -> None:
    user_id, _ = await _seed_game_user_agent(reset_db)
    r = await client.get(
        "/me/strategy-profiles", cookies=_signed_in_cookies(user_id)
    )
    assert r.status_code == 404
