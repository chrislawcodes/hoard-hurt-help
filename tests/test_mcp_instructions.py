"""Tests for the MCP get_instructions tool."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.models import Base
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match
from app.models.player import Player
from tests.factories import make_user


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _fake_connection(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, id=1)


async def _seat_agent(
    db: AsyncSession,
    *,
    user_id: int,
    match_id: str,
    agent_name: str,
    seat_name: str,
    game: str,
    strategy_text: str,
    total_rounds: int,
    turns_per_round: int,
) -> tuple[Agent, Match, Player]:
    agent = Agent(
        user_id=user_id,
        provider=ConnectionProvider.CLAUDE,
        kind=AgentKind.AI,
        name=agent_name,
        game=game,
        status=AgentStatus.ACTIVE,
    )
    db.add(agent)
    await db.flush()
    version = AgentVersion(
        agent_id=agent.id,
        version_no=1,
        model="claude-haiku-4-5",
        strategy_text=strategy_text,
    )
    db.add(version)
    await db.flush()
    agent.current_version_id = version.id
    now = datetime.now(timezone.utc)
    match = Match(
        id=match_id,
        name=match_id,
        game=game,
        state=GameState.ACTIVE,
        scheduled_start=now,
        started_at=now,
        total_rounds=total_rounds,
        turns_per_round=turns_per_round,
        current_round=1,
        current_turn=1,
    )
    db.add(match)
    await db.flush()
    player = Player(
        match_id=match.id,
        user_id=user_id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
    )
    db.add(player)
    await db.flush()
    return agent, match, player


@pytest.mark.asyncio
async def test_get_instructions_returns_sections_and_tool_format_for_selected_agent(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server import server

    async with session_factory() as db:
        user = await make_user(db)
        alpha, alpha_match, alpha_player = await _seat_agent(
            db,
            user_id=user.id,
            match_id="M_HHH",
            agent_name="Alpha",
            seat_name="Alpha",
            game="hoard-hurt-help",
            strategy_text="alpha strategy",
            total_rounds=7,
            turns_per_round=7,
        )
        beta, beta_match, beta_player = await _seat_agent(
            db,
            user_id=user.id,
            match_id="M_LD",
            agent_name="Beta",
            seat_name="Beta",
            game="liars-dice",
            strategy_text="beta strategy",
            total_rounds=64,
            turns_per_round=256,
        )
        await db.commit()

    async def fake_resolve_oauth_connection(db: object, token: object) -> tuple[object, object, SimpleNamespace]:
        return object(), object(), _fake_connection(user.id)

    monkeypatch.setattr(server, "_resolve_oauth_connection", fake_resolve_oauth_connection)

    async with session_factory() as db:
        hhh_text = await server.get_instructions(agent_id=alpha.id, token=object(), db=db)
        assert "## The rules" in hhh_text
        assert "Hoard-Hurt-Help" in hhh_text
        assert "You are \"Alpha\"" in hhh_text
        assert "alpha strategy" in hhh_text
        assert "submit_talk" in hhh_text
        assert "submit_action" in hhh_text
        assert "JSON" not in hhh_text
        assert "RESPONSE FORMAT" not in hhh_text

        ld_text = await server.get_instructions(agent_id=beta.id, token=object(), db=db)
        assert "## The rules" in ld_text
        assert "Liar's Dice" in ld_text
        assert "hidden dice" in ld_text
        assert "You are \"Beta\"" in ld_text
        assert "beta strategy" in ld_text
        assert "Hoard-Hurt-Help" not in ld_text


@pytest.mark.asyncio
async def test_get_instructions_multiple_active_agents_returns_agent_note(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server import server

    async with session_factory() as db:
        user = await make_user(db)
        alpha, _alpha_match, _alpha_player = await _seat_agent(
            db,
            user_id=user.id,
            match_id="M_HHH",
            agent_name="Alpha",
            seat_name="Alpha",
            game="hoard-hurt-help",
            strategy_text="alpha strategy",
            total_rounds=7,
            turns_per_round=7,
        )
        beta, _beta_match, _beta_player = await _seat_agent(
            db,
            user_id=user.id,
            match_id="M_LD",
            agent_name="Beta",
            seat_name="Beta",
            game="liars-dice",
            strategy_text="beta strategy",
            total_rounds=64,
            turns_per_round=256,
        )
        await db.commit()

    async def fake_resolve_oauth_connection(db: object, token: object) -> tuple[object, object, SimpleNamespace]:
        return object(), object(), _fake_connection(user.id)

    monkeypatch.setattr(server, "_resolve_oauth_connection", fake_resolve_oauth_connection)

    async with session_factory() as db:
        text = await server.get_instructions(token=object(), db=db)
        assert "You have multiple agents" in text
        assert str(alpha.id) in text
        assert str(beta.id) in text
        assert "## How to answer" in text
        assert "## The rules" not in text


@pytest.mark.asyncio
async def test_get_instructions_no_active_game_returns_short_note(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server import server

    async with session_factory() as db:
        user = await make_user(db)
        await db.commit()

    async def fake_resolve_oauth_connection(db: object, token: object) -> tuple[object, object, SimpleNamespace]:
        return object(), object(), _fake_connection(user.id)

    monkeypatch.setattr(server, "_resolve_oauth_connection", fake_resolve_oauth_connection)

    async with session_factory() as db:
        text = await server.get_instructions(token=object(), db=db)
        assert "No active game yet" in text
        assert "## The rules" not in text
        assert "## Your strategy" not in text
