"""The settings that let a poll be held long enough to measure the real ceiling.

The production edge's request timeout has never been measured for this service —
Railway documents 300s, its own support forum reports connections cut at 20-90s,
and today's 40s was picked against an assumed ~100s proxy limit. Every
hold-length decision depends on the true number, and the only way to find it is
to hold a real request through the real edge.

Both settings default to the shipped behaviour, so these tests pin two things:
that the default really is unchanged, and that switching the flag on does what it
claims — including keeping the waiting agent's liveness stamps fresh, without
which a hold longer than 90s makes the connection read as dead for turn routing
and it is refused the very turn the hold is waiting to serve.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sqlalchemy.ext.asyncio import AsyncEngine

from app.engine import agent_idle
from app.engine.agent_idle import POLL_IN_PLAY_SECONDS, IdleStatus, pace_idle
from app.engine.connection_activity import mark_still_holding
from app.models import Base
from app.models.connection import Connection
from tests.factories import make_connection, make_user


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Shadows conftest's factory to create the schema first."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)

# One IdleStatus per lane pace_idle distinguishes.
_LIVE_GAME = IdleStatus(
    has_game=True, has_live_game=True, seconds_to_next_start=None,
    idle_seconds=0, should_stop=False, stop_reason=None,
)
_FINAL_APPROACH = IdleStatus(
    has_game=True, has_live_game=False, seconds_to_next_start=30,
    idle_seconds=0, should_stop=False, stop_reason=None,
)
_NEAR_START = IdleStatus(
    has_game=True, has_live_game=False, seconds_to_next_start=200,
    idle_seconds=0, should_stop=False, stop_reason=None,
)
_FAR_START = IdleStatus(
    has_game=True, has_live_game=False, seconds_to_next_start=540,
    idle_seconds=0, should_stop=False, stop_reason=None,
)
_NO_GAME = IdleStatus(
    has_game=False, has_live_game=False, seconds_to_next_start=None,
    idle_seconds=120, should_stop=False, stop_reason=None,
)
_EVERY_LANE = (_LIVE_GAME, _FINAL_APPROACH, _NEAR_START, _FAR_START, _NO_GAME)


def test_default_leaves_the_waiting_lanes_returning_no_hold() -> None:
    """The shipped default must be byte-identical to today's behaviour, so
    merging this changes nothing until the flag is deliberately switched on."""
    assert agent_idle.HOLD_IDLE_LANES is False
    assert pace_idle(_FAR_START)[0] == 0.0
    assert pace_idle(_NEAR_START)[0] == 0.0
    assert pace_idle(_NO_GAME)[0] == 0.0


def test_flag_on_holds_every_lane_for_the_configured_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag on, no lane may return a zero hold — a "wait N seconds"
    reply is the one instruction an AI client has no mechanism to obey."""
    monkeypatch.setattr(agent_idle, "HOLD_IDLE_LANES", True)
    monkeypatch.setattr(agent_idle, "LONG_POLL_HOLD_SECONDS", 240)

    for idle in _EVERY_LANE:
        hold, next_poll = pace_idle(idle)
        assert hold == 240.0, f"lane {idle} returned a {hold}s hold"
        # ...and the client is told to come straight back, which is the only
        # wait it can actually perform.
        assert next_poll == POLL_IN_PLAY_SECONDS


def test_hold_length_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the setting: the length is dialable in production
    without a deploy per measurement attempt."""
    monkeypatch.setattr(agent_idle, "HOLD_IDLE_LANES", True)
    for configured in (40, 120, 300):
        monkeypatch.setattr(agent_idle, "LONG_POLL_HOLD_SECONDS", configured)
        assert pace_idle(_FAR_START)[0] == float(configured)


async def test_holding_refreshes_liveness_without_counting_a_second_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A held poll must keep both liveness stamps fresh, and must NOT bump
    ``api_call_count`` — one held poll is one paid inference however many times
    the server re-checks inside it."""
    stale = datetime.now(timezone.utc) - timedelta(seconds=300)
    async with session_factory() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user, last_seen_at=stale)
        connection.last_polled_at = stale
        connection.api_call_count = 7
        await db.commit()
        connection_id = connection.id

        await mark_still_holding(db, connection)

        refreshed = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        # Both windows the hold would otherwise age out of.
        assert (datetime.now(timezone.utc) - refreshed.last_seen_at.replace(
            tzinfo=timezone.utc
        )).total_seconds() < 30
        assert (datetime.now(timezone.utc) - refreshed.last_polled_at.replace(
            tzinfo=timezone.utc
        )).total_seconds() < 30
        # The cost signal is untouched.
        assert refreshed.api_call_count == 7
