"""Slice 4a: the agent-settings preferred-model picker route."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import provider_for_model
from app.engine.model_provider_match import default_model_for_provider, resolve_seat_model
from app.models.agent import Agent
from tests.conftest import session_cookie
from tests.factories import make_agent, make_user


async def _seed_user_and_agent_ids(reset_db: async_sessionmaker) -> tuple[int, int]:
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
    user_id, agent_id = await _seed_user_and_agent_ids(reset_db)
    cookies = {"hhh_session": session_cookie(user_id)}

    r = await client.post(
        f"/me/agents/{agent_id}/set-model",
        data={"preferred_model": "claude-opus-5"},
        cookies=cookies,
    )
    assert r.status_code == 303, r.text
    assert await _preferred(reset_db, agent_id) == "claude-opus-5"

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
    user_id, agent_id = await _seed_user_and_agent_ids(reset_db)
    r = await client.post(
        f"/me/agents/{agent_id}/set-model",
        data={"preferred_model": "totally-made-up-model"},
        cookies={"hhh_session": session_cookie(user_id)},
    )
    assert r.status_code == 400
    assert await _preferred(reset_db, agent_id) is None


async def test_set_model_accepts_fable(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """Fable is pickable, and picking it does not disturb the provider default.

    Both halves matter. The allowlist is what the picker offers AND what this
    route validates against, so a model missing from it is simply unselectable —
    which is what blocked putting the No Playbook control on Fable. And because
    the same list's FIRST entry is the provider default, adding one in the wrong
    place would move every unset Claude seat onto Fable without anything failing.
    """
    user_id, agent_id = await _seed_user_and_agent_ids(reset_db)

    r = await client.post(
        f"/me/agents/{agent_id}/set-model",
        data={"preferred_model": "claude-fable-5-1"},
        cookies={"hhh_session": session_cookie(user_id)},
    )
    assert r.status_code == 303, r.text
    assert await _preferred(reset_db, agent_id) == "claude-fable-5-1"

    # It belongs to Claude, so a Claude seat keeps it rather than falling back.
    assert provider_for_model("claude-fable-5-1") == "claude"
    assert resolve_seat_model("claude", "claude-fable-5-1") == "claude-fable-5-1"
    assert default_model_for_provider("claude") == "claude-haiku-4-5"
