"""Slice 0 — human-player identity.

A human seat is an Agent with kind=human: no connection, no provider, one frozen
version, idempotent per (user, game), and excluded from the AI-agent surfaces.
"""

from __future__ import annotations

from sqlalchemy import select

from app.engine.human_player import HUMAN_VERSION_MODEL, get_or_create_human_agent
from app.models import (
    Agent,
    AgentKind,
    AgentVersion,
    User,
)

GAME = "hoard-hurt-help"


async def _make_user(db, i: int = 0) -> User:
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@t.com",
        handle=f"alice{i}",
        handle_key=f"alice{i}",
        name=f"Alice {i}",
    )
    db.add(user)
    await db.flush()
    return user


async def test_creates_human_agent_with_frozen_version(db) -> None:
    user = await _make_user(db)

    agent, version = await get_or_create_human_agent(db, user, GAME)

    assert agent.kind == AgentKind.HUMAN
    assert agent.provider is None
    assert agent.game == GAME
    assert agent.user_id == user.id
    assert agent.current_version_id == version.id

    assert version.model == HUMAN_VERSION_MODEL
    assert version.strategy_text == ""
    assert version.frozen_at is not None
    assert version.version_no == 1


async def test_is_idempotent_per_user_and_game(db) -> None:
    user = await _make_user(db)

    a1, v1 = await get_or_create_human_agent(db, user, GAME)
    a2, v2 = await get_or_create_human_agent(db, user, GAME)

    assert a1.id == a2.id
    assert v1.id == v2.id

    agents = (
        (await db.execute(select(Agent).where(Agent.kind == AgentKind.HUMAN)))
        .scalars()
        .all()
    )
    versions = (await db.execute(select(AgentVersion))).scalars().all()
    assert len(agents) == 1
    assert len(versions) == 1


async def test_name_is_unique_against_existing_agents(db) -> None:
    user = await _make_user(db)
    # An existing agent already owns the user's handle as its name.
    db.add(Agent(user_id=user.id, name=user.handle, kind=AgentKind.AI, game=GAME))
    await db.flush()

    agent, _ = await get_or_create_human_agent(db, user, GAME)

    assert agent.name != user.handle  # uniquified, no IntegrityError


async def test_separate_human_agent_per_game(db) -> None:
    user = await _make_user(db)

    a_pd, _ = await get_or_create_human_agent(db, user, "hoard-hurt-help")
    a_ld, _ = await get_or_create_human_agent(db, user, "liars-dice")

    assert a_pd.id != a_ld.id
    assert a_pd.game != a_ld.game


async def test_human_excluded_from_ai_agent_query(db) -> None:
    """The AI-agent surfaces (capacity, routing, agent list) filter kind==AI."""
    user = await _make_user(db)
    human, _ = await get_or_create_human_agent(db, user, GAME)

    ai_agent_ids = (
        (
            await db.execute(
                select(Agent.id).where(
                    Agent.user_id == user.id, Agent.kind == AgentKind.AI
                )
            )
        )
        .scalars()
        .all()
    )
    assert human.id not in ai_agent_ids
