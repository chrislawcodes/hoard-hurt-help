"""Fan-out tests: long-poll pacing, idle cadence, and rate/call-count tracking.

Split from test_agent_next_turn_fanout.py — covers no_game/waiting cadence,
the long-poll hold, per-agent pacing scope, api_call_count bookkeeping, and
the pooled-connection hygiene of the hold itself.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.tokens import generate_turn_token
from app.models.connection import Connection
from app.models.match import GameState, Match
from app.models.turn import Turn
from tests.agent_next_turn_fanout_support import (
    _create_match_with_turn,
    _seat_agent,
    app,
    engine,
    scoped_client,
    session_factory,
)
from tests.factories import make_connection, make_user

# `app` and `engine` are never named directly by a test here, but scoped_client
# (used by every test below) depends on the whole app -> session_factory ->
# engine fixture chain -- see the support module's docstring for why all four
# have to be imported together. Listing them in __all__ tells ruff they are
# used (the same pattern tests/conftest.py already uses for make_user).
__all__ = ["app", "engine", "scoped_client", "session_factory"]


async def test_no_game_returns_no_game_immediately_with_idle_cadence(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A connection with NO game at all gets 'no_game' (not 'waiting') at once,
    carrying an idle count and the slow 5-minute idle cadence. The plural endpoint
    does the same. A freshly-connected caller is not told to stop yet."""
    async with session_factory() as db:
        user = await make_user(db)
        _connection, key = await make_connection(db, user)
        await db.commit()

    loop = asyncio.get_event_loop()
    started = loop.time()
    single = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    elapsed = loop.time() - started
    assert single.status_code == 200, single.text
    body = single.json()
    assert body["status"] == "no_game"
    # Returned immediately (no long-poll hold) and advised the slow idle cadence.
    assert elapsed < 0.5
    assert body["next_poll_after_seconds"] == 300
    # Just connected — idle clock barely started, so don't stop yet.
    assert body["should_stop"] is False
    assert body["idle_seconds"] < 60
    assert "stop_reason" not in body

    batch = await scoped_client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    bbody = batch.json()
    assert bbody["status"] == "no_game"
    assert bbody["next_poll_after_seconds"] == 300
    assert bbody["should_stop"] is False


async def test_long_poll_returns_waiting_after_window_when_seated_no_open_turn(
    scoped_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seated in an active game but with no open turn: a turn could open any
    moment, so the server long-polls — it holds the request open, then returns
    'waiting'. We shrink the server's hold so the test stays fast and assert the
    call actually spent close to the window before giving up."""
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Active match, agent seated, but NO open turn yet — a turn is still coming.
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_WAIT",
            name="match-M_WAIT",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        await db.commit()

    loop = asyncio.get_event_loop()
    started = loop.time()
    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    elapsed = loop.time() - started
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting"
    # It held roughly the whole window before returning (not an instant reply).
    assert elapsed >= 0.35
    # After a long-poll the client should re-open promptly (the hold was the wait).
    assert body["next_poll_after_seconds"] <= 5


async def test_long_poll_returns_promptly_when_a_turn_opens(
    scoped_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a turn opens partway through the hold, the long-poll returns it the
    moment its next re-check sees it — well before the full window elapses."""
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Seat the agent in an active match, but with NO open turn yet.
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_0800",
            name="match-M_0800",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        agent, _version, _player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        await db.commit()

    async def open_turn_soon() -> None:
        # Open the turn shortly after the long-poll begins holding.
        await asyncio.sleep(0.15)
        async with session_factory() as db:
            db.add(
                Turn(
                    match_id="M_0800",
                    round=1,
                    turn=1,
                    turn_token=generate_turn_token(),
                    opened_at=datetime.now(timezone.utc),
                    deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                    phase="act",
                )
            )
            await db.commit()

    loop = asyncio.get_event_loop()
    started = loop.time()
    # Long hold window, fast re-check interval: the response should come back when
    # the turn opens (~0.15s), not at the 5s window.
    opener = asyncio.create_task(open_turn_soon())
    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    await opener
    elapsed = loop.time() - started
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["match_id"] == "M_0800"
    assert body["agent_id"] == agent.id
    # Returned promptly after the turn opened — nowhere near the full 5s window.
    assert elapsed < 2.0


async def test_pacing_is_agent_scoped_when_a_loop_asks_for_one_agent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A per-agent loop must pace off ITS own soonest game, not a busier sibling.

    Agent A's game is 25 min off; sibling agent B is live. Connection-wide, a live
    game means long-poll — but the loop scoped to A should see no live game and use
    the cheap 5-minute waiting cadence, so A's loop doesn't burn the fast in-play
    rate waiting on B's game."""
    from app.engine.agent_idle import compute_idle_status, pace_idle

    async with session_factory() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        far = Match(
            id="M_FAR",
            name="match-M_FAR",
            state=GameState.SCHEDULED,
            scheduled_start=now + timedelta(minutes=25),
            per_turn_deadline_seconds=60,
            current_round=0,
            current_turn=0,
        )
        live = Match(
            id="M_LIVE",
            name="match-M_LIVE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add_all([far, live])
        await db.flush()
        agent_a, _va, _pa = await _seat_agent(
            db, user=user, connection=connection, match=far,
            seat_name=f"{user.handle}/A", agent_name="A",
            model="claude-sonnet-5", strategy_text="s",
        )
        await _seat_agent(
            db, user=user, connection=connection, match=live,
            seat_name=f"{user.handle}/B", agent_name="B",
            model="claude-sonnet-5", strategy_text="s",
        )
        await db.commit()
        connection_id = connection.id
        agent_a_id = agent_a.id

    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        # Connection-wide: B is live → long-poll.
        whole = await compute_idle_status(db, conn)
        assert whole.has_live_game is True
        assert pace_idle(whole)[0] > 0  # holds the line open

        # Scoped to agent A: its only game is 25 min off → no hold, 5-min cadence.
        scoped = await compute_idle_status(db, conn, agent_id=agent_a_id)
        assert scoped.has_live_game is False
        assert scoped.seconds_to_next_start is not None
        assert scoped.seconds_to_next_start > 600
        hold, next_poll = pace_idle(scoped)
        assert hold == 0.0
        assert next_poll == 300


async def test_api_call_count_increments_and_turn_count_on_real_submit(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Every authenticated call bumps api_call_count; a real (non-defaulted)
    submit bumps turns_played. The detail page reads these raw counts."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, turn = await _create_match_with_turn(db, "M_0900", deadline_seconds=60)
        agent, _version, _player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        await db.commit()
        connection_id = connection.id

    # One poll that serves a turn.
    served = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["status"] == "your_turn"
    agent_turn_token = body["agent_turn_token"]
    turn_token = body["turn_token"]

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        assert stored.api_call_count == 1
        assert stored.turns_played == 0

    submit = await scoped_client.post(
        f"/api/matches/M_0900/submit?agent_turn_token={agent_turn_token}",
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn_token,
            "action": "HOARD",
            "target_id": None,
            "message": "mine",
            "thinking": "",
        },
    )
    assert submit.status_code == 202, submit.text

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        # The submit was one more authenticated call (count now 2) and one real
        # turn played.
        assert stored.api_call_count == 2
        assert stored.turns_played == 1


async def test_no_game_after_idle_window_tells_client_to_stop(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A connection with no game that has been idle past the ~10-min window gets
    should_stop=True with a stop_reason, so an interactive client stops polling."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Back-date every idle anchor well past the 10-minute window.
        long_ago = datetime.now(timezone.utc) - timedelta(minutes=20)
        connection.first_connected_at = long_ago
        connection.mcp_connected_at = long_ago
        connection.created_at = long_ago
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_game"
    assert body["should_stop"] is True
    assert body["stop_reason"] == "idle_timeout"
    assert body["idle_seconds"] >= 600


async def test_seated_in_active_game_is_waiting_not_no_game(
    scoped_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even long after the idle window, a caller seated in an active game (turn not
    open) is 'waiting' — a turn is coming — and is never told to stop."""
    # Shrink the server's long-poll hold so the test skips the full production
    # wait; the assertions below (waiting, no stop hint) are unchanged.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        connection.first_connected_at = datetime.now(timezone.utc) - timedelta(hours=2)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_SEATED",
            name="match-M_SEATED",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        await db.commit()

    # No open turn yet -> waiting (not no_game), and no stop hint.
    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting"
    assert "should_stop" not in body


async def test_scheduled_game_keeps_caller_waiting_not_no_game(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A caller seated in a not-yet-started (scheduled) game is 'waiting' — the
    game is about to start, so never 'no_game' and never told to stop."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        connection.first_connected_at = datetime.now(timezone.utc) - timedelta(hours=2)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_SCHED",
            name="match-M_SCHED",
            state=GameState.SCHEDULED,
            scheduled_start=now + timedelta(minutes=5),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting"
    assert "should_stop" not in body


class _SleepSampler:
    """Stand-in for the module's ``asyncio``, so only the hold's own waits are
    sampled — patching the real ``asyncio.sleep`` would also catch the test
    client's internals."""

    def __init__(self, sessions: list[AsyncSession]) -> None:
        self._sessions = sessions
        self.in_transaction_while_waiting: list[bool] = []

    def get_event_loop(self) -> asyncio.AbstractEventLoop:
        return asyncio.get_event_loop()

    async def sleep(self, delay: float) -> None:
        self.in_transaction_while_waiting.append(
            any(session.in_transaction() for session in self._sessions)
        )
        await asyncio.sleep(delay)


async def test_hold_frees_its_db_connection_between_re_checks(
    scoped_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long-poll must leave no open DB transaction while it waits between
    re-checks. A session with a transaction open keeps its pooled connection
    checked out, so a hold that leaves one open pins one connection for its whole
    duration — and a handful of agents waiting at once drains the pool."""
    # The hold is wall-clock bounded: `while loop.time() < deadline` runs one
    # sleep plus one DB re-check per pass, so the number of passes that fit in
    # the budget depends on how fast this machine completes a re-check. At the
    # original 0.3s budget that was ~6 passes locally but ONE on a loaded CI
    # runner, where a re-check costs more than the 0.05s interval — which failed
    # the "ran at least 3 times" guard below while the invariant it guards was
    # perfectly fine. The budget is generous so the pass count is set by the
    # interval rather than by the runner's speed; the request still returns as
    # soon as the hold ends, so this costs the suite ~2s, not a hang.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 2.0)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # Seated in an active match with no open turn: the server long-polls.
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_POOL",
            name="match-M_POOL",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match)
        await db.flush()
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        await db.commit()

    # Track every session the request opens so we can inspect them mid-wait.
    opened: list[AsyncSession] = []

    def tracking_factory() -> AsyncSession:
        session = session_factory()
        opened.append(session)
        return session

    monkeypatch.setattr("app.db.SessionLocal", tracking_factory)
    sampler = _SleepSampler(opened)
    monkeypatch.setattr("app.engine.agent_play_next_turn.asyncio", sampler)

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "waiting"
    # The hold really did re-check several times (so the assertion below is not
    # passing vacuously)...
    assert len(sampler.in_transaction_while_waiting) >= 3
    # ...and never sat on an open transaction while waiting between checks.
    assert not any(sampler.in_transaction_while_waiting)
