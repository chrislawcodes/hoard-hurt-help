"""The two forms of "can this agent take a seat" must agree on every agent.

`playable_agent_filter()` is SQL that selects agents to serve turns to.
`seat_block()` inspects one loaded Agent and says why it cannot play. A WHERE
clause and an if-statement cannot be the same code, so the only thing keeping
them honest is a test that runs both over the same agents and compares.

That is not hypothetical. Before this module the two halves DID disagree: the
join path selected `kind=AI + not archived` while every turn-serving query also
required `status=ACTIVE`. A paused agent therefore passed the join door, took a
seat, and was skipped by every serving query for the rest of the match.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engine.agent_playability import playable_agent_filter, seat_block
from app.models.agent import Agent, AgentKind, AgentStatus
from tests.factories import make_agent, make_user

pytestmark = pytest.mark.asyncio


async def test_the_sql_filter_and_the_object_check_agree_on_every_agent(reset_db) -> None:
    """Build one agent per (kind x status x archived) and compare both forms.

    Fails the moment someone adds a reason an agent cannot play and updates only
    one of the two -- which is exactly how the paused bug happened.
    """
    combos = list(
        itertools.product(
            [AgentKind.AI, AgentKind.BOT, AgentKind.HUMAN],
            [AgentStatus.ACTIVE, AgentStatus.PAUSED],
            [False, True],
        )
    )
    async with reset_db() as db:
        user = await make_user(db)
        made: dict[int, tuple[AgentKind, AgentStatus, bool]] = {}
        for i, (kind, status, archived) in enumerate(combos):
            agent, _version = await make_agent(db, user, name=f"a{i}", kind=kind, status=status)
            if archived:
                agent.archived_at = datetime.now(timezone.utc)
            made[agent.id] = (kind, status, archived)
        await db.commit()

        selectable = set(
            (
                await db.execute(select(Agent.id).where(*playable_agent_filter()))
            ).scalars().all()
        )
        agents = (await db.execute(select(Agent))).scalars().all()

    assert len(made) == len(combos)
    for agent in agents:
        if agent.id not in made:
            continue  # factories may create extra rows (e.g. a human seat)
        blocked = seat_block(agent) is not None
        in_sql = agent.id in selectable
        assert blocked != in_sql, (
            f"disagreement for {made[agent.id]}: seat_block says "
            f"{'BLOCKED' if blocked else 'ok'} but the SQL filter "
            f"{'kept' if in_sql else 'dropped'} it"
        )


async def test_a_paused_ai_agent_is_blocked_and_says_how_to_fix_it(reset_db) -> None:
    """The live bug, pinned. A paused agent must not be seatable, and the block
    has to carry a link -- the picker renders it instead of writing instructions."""
    async with reset_db() as db:
        user = await make_user(db)
        agent, _version = await make_agent(db, user, status=AgentStatus.PAUSED)
        await db.commit()
        agent_id = agent.id

        block = seat_block(agent)
        assert block is not None
        assert block.reason == "Paused"
        assert block.fix_label == "resume it"
        assert block.fix_path == f"/me/agents/{agent_id}"


async def test_an_active_ai_agent_is_not_blocked(reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        agent, _version = await make_agent(db, user, status=AgentStatus.ACTIVE)
        await db.commit()
        assert seat_block(agent) is None
