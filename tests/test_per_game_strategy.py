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
from app.models.connection import ConnectionProvider
from app.models.agent import AgentStatus
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


def test_default_strategies_do_not_repeat_base_instructions() -> None:
    module = get_game_module("hoard-hurt-help")
    strategies = [module.default_strategy(), *(preset.prompt for preset in module.strategy_presets())]
    repeated_base_phrases = (
        "You are playing Hoard-Hurt-Help",
        "Read the full rules",
        "full raw record",
        "read the chat",
        "TALK PHASE",
        "target_id",
    )
    for strategy in strategies:
        assert "Prioritize round wins" in strategy
        for phrase in repeated_base_phrases:
            assert phrase not in strategy


def test_agent_base_prompt_contains_shared_instructions_not_strategy() -> None:
    module = get_game_module("hoard-hurt-help")
    prompt = module.agent_base_prompt(
        your_agent_id="Alpha",
        all_agent_ids=["Alpha", "Beta"],
    )
    assert 'as agent "Alpha"' in prompt
    assert "The chat is part of the game" in prompt
    assert "HISTORY" not in prompt
    assert "max 200 chars" in prompt
    assert 'Agents you may target: ["Beta"]' in prompt
    assert prompt.index("Agents you may target") < prompt.index("RESPONSE FORMAT:")
    assert prompt.endswith("counts as a missed move.")
    assert "Prioritize round wins" not in prompt


@pytest.mark.asyncio
async def test_join_with_custom_strategy_seeds_it(client, reset_db) -> None:
    user_id, _connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
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
    user_id, _connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
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
    user_id, _connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
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
async def test_create_form_has_no_model_picker_but_keeps_presets(
    client, reset_db
) -> None:
    # Agents are decoupled from a model/provider — the create form has no model or
    # provider picker. It still offers strategy presets and a free-text strategy.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    r = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert 'name="provider"' not in r.text
    assert 'name="model"' not in r.text
    assert "<optgroup" not in r.text
    assert 'name="strategy_preset"' not in r.text
    assert 'name="strategy_text"' in r.text
    assert 'href="/games/hoard-hurt-help/agent-instructions"' in r.text
    assert 'data-preset-id="tit_for_tat"' in r.text
    assert 'data-preset-id="custom"' in r.text
    assert r.text.index('data-preset-id="tit_for_tat"') < r.text.index('data-preset-id="custom"')
    tit_snippet = r.text[r.text.index('data-preset-id="tit_for_tat"') : r.text.index('data-preset-id="tit_for_tat"') + 220]
    assert 'aria-pressed="true"' in tit_snippet


@pytest.mark.asyncio
async def test_agent_instructions_page_shows_canonical_base_prompt(client, reset_db) -> None:
    r = await client.get("/games/hoard-hurt-help/agent-instructions")
    assert r.status_code == 200
    assert "Base instructions" in r.text
    assert "Your editable strategy is added separately" in r.text
    assert "The chat is part of the game" in r.text
    assert "max 200 chars" in r.text
    assert "X-Agent-Key" not in r.text
    assert "Prioritize round wins" not in r.text


@pytest.mark.asyncio
async def test_create_agent_page_without_any_connection_shows_full_form(
    client, reset_db
) -> None:
    # No provider connected at all: the create-agent page still renders the full
    # design form so the player can name the agent and save a strategy before
    # doing the technical setup. There is no model/provider picker.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    r = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Connect an AI client first" not in r.text
    assert 'name="model"' not in r.text
    assert "<optgroup" not in r.text
    assert 'name="strategy_text"' in r.text


@pytest.mark.asyncio
async def test_create_agent_without_live_connection_still_creates_agent(
    client, reset_db
) -> None:
    # Creating an agent always works (name + strategy). Post-create lands on the
    # lobby, where joining a game walks the user through connecting an AI.
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()

    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "strategy_text": "CUSTOM: always cooperate.",
        },
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/games/hoard-hurt-help"

    async with reset_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.user_id == user.id, Agent.name == "Atlas"))
        ).scalar_one()
    assert agent.provider is None
    assert agent.status == AgentStatus.ACTIVE


@pytest.mark.asyncio
async def test_create_agent_with_next_returns_to_next_target(
    client, reset_db
) -> None:
    # When ?next is present AND the agent's provider is already set up, creation
    # returns straight there. (Claude is set up here, so it's a direct hop.)
    join_url = "/games/hoard-hurt-help/matches/G_001/join"
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "CUSTOM: always cooperate.",
            "next": join_url,
        },
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    loc = r.headers["location"]
    assert loc == join_url


@pytest.mark.asyncio
async def test_strategy_profiles_route_removed(client, reset_db) -> None:
    user_id, _ = await _seed_game_user_agent(reset_db)
    r = await client.get(
        "/me/strategy-profiles", cookies=_signed_in_cookies(user_id)
    )
    assert r.status_code == 404
