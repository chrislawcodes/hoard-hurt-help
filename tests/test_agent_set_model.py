"""Slice 4a: the agent-settings preferred-model picker route."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent import Agent
from tests.conftest import session_cookie
from tests.factories import make_agent, make_user


async def _seed_agent(reset_db: async_sessionmaker) -> tuple[int, int]:
    async with reset_db() as db:
        user = await make_user(db, 0)
        agent, _ = await make_agent(db, user, name="picker-agent")
        await db.commit()
        return user.id, agent.id


async def _preferred(reset_db: async_sessionmaker, agent_id: int) -> str | None:
    async with reset_db() as db:
        return (
            await db.execute(select(Agent.preferred_model).where(Agent.id == agent_id))
        ).scalar_one()


async def test_set_model_sets_and_clears(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    user_id, agent_id = await _seed_agent(reset_db)
    cookies = {"hhh_session": session_cookie(user_id)}

    r = await client.post(
        f"/me/agents/{agent_id}/set-model",
        data={"preferred_model": "claude-opus-4-8"},
        cookies=cookies,
    )
    assert r.status_code == 303, r.text
    assert await _preferred(reset_db, agent_id) == "claude-opus-4-8"

    # Empty submission clears it back to the provider default.
    r = await client.post(
        f"/me/agents/{agent_id}/set-model",
        data={"preferred_model": ""},
        cookies=cookies,
    )
    assert r.status_code == 303, r.text
    assert await _preferred(reset_db, agent_id) is None


async def test_set_model_rejects_unknown_model(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    user_id, agent_id = await _seed_agent(reset_db)
    r = await client.post(
        f"/me/agents/{agent_id}/set-model",
        data={"preferred_model": "totally-made-up-model"},
        cookies={"hhh_session": session_cookie(user_id)},
    )
    assert r.status_code == 400
    assert await _preferred(reset_db, agent_id) is None
