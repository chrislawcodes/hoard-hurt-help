"""Milestones recorded from explicit call sites rather than ORM listeners.

Three milestones cannot come from an insert listener, because they are triggered
by a field *update* or by a decision only the request knows about:

* ``picked_handle`` — a column on an existing user changes.
* ``ai_connected`` — a connection's first-connect timestamp is stamped.
* ``played_turn`` — a submission row is written on several paths, only some of
  which are a real person or a real agent choosing a move.

The first two tests below cover the cases that three earlier designs got wrong:
a human player, and an MCP user who connects before they have an agent.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engine.connection_activity import mark_seen
from app.engine.tokens import bot_key_lookup
from app.models.agent import AgentKind
from app.models.user_milestone import MilestoneKind, UserMilestone
from tests.factories import make_agent, make_connection, make_user


async def _reached(db, user_id: int) -> set[MilestoneKind]:
    rows = (
        (
            await db.execute(
                select(UserMilestone.milestone).where(UserMilestone.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


@pytest.mark.asyncio
async def test_a_human_player_reaches_the_play_milestones(db) -> None:
    """The default path for a brand-new user, and the one three designs missed.

    A human seat has no connection and never will. Under the old strict-funnel
    design a person playing every day rendered as stuck at "picked a handle".
    """
    user = await make_user(db)
    await make_agent(db, user, kind=AgentKind.HUMAN)
    await db.commit()

    reached = await _reached(db, user.id)
    assert MilestoneKind.SET_UP_HUMAN_PLAY in reached
    # And crucially, not being AI-connected must not remove anything.
    assert MilestoneKind.AI_CONNECTED not in reached
    assert MilestoneKind.SIGNED_UP in reached


@pytest.mark.asyncio
async def test_connecting_before_building_an_agent_records_both(db) -> None:
    """Order must not matter.

    An MCP user gets a live connection before they have an agent or a handle.
    Any design that required the earlier rungs first dropped them entirely.
    """
    user = await make_user(db)
    connection, plain_key = await make_connection(db, user, key="sk_conn_test_ordering")
    connection.first_connected_at = None
    await db.commit()

    await mark_seen(db, connection, key_hash=bot_key_lookup(plain_key))
    assert MilestoneKind.AI_CONNECTED in await _reached(db, user.id)

    # The agent arrives afterwards; both are recorded, neither is suppressed.
    await make_agent(db, user, kind=AgentKind.AI)
    await db.commit()

    reached = await _reached(db, user.id)
    assert MilestoneKind.AI_CONNECTED in reached
    assert MilestoneKind.SET_UP_AI_AGENT in reached


@pytest.mark.asyncio
async def test_first_connect_is_recorded_once(db) -> None:
    """mark_seen runs on every authenticated call; the milestone is the first."""
    user = await make_user(db)
    connection, plain_key = await make_connection(db, user, key="sk_conn_test_repeat")
    connection.first_connected_at = None
    await db.commit()

    key_hash = bot_key_lookup(plain_key)
    for _ in range(3):
        await mark_seen(db, connection, key_hash=key_hash)

    rows = (
        (
            await db.execute(
                select(UserMilestone).where(
                    UserMilestone.user_id == user.id,
                    UserMilestone.milestone == MilestoneKind.AI_CONNECTED,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_an_already_connected_connection_records_nothing_new(db) -> None:
    """A connection that was already live is not a new first-connect."""
    user = await make_user(db)
    connection, plain_key = await make_connection(
        db,
        user,
        key="sk_conn_test_already",
        first_connected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    await db.commit()
    assert connection.first_connected_at is not None

    before = await _reached(db, user.id)
    await mark_seen(db, connection, key_hash=bot_key_lookup(plain_key))
    assert await _reached(db, user.id) == before
