"""Overdue-turn sweeper tests: freeze detection, force-advance heals, and skips."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.aware_datetime import ensure_aware
from app.engine import overdue_sweeper, scheduler
from app.engine.overdue_sweeper import sweep_overdue_turns
from app.models import Match, GameState, Player, Turn, TurnMessage, TurnSubmission, User
from tests.factories import make_bot


def _same_session_factory(session):
    """A session_factory shim that hands back an already-open session.

    The in-memory test engine doesn't share one connection across sessions, so
    code that opens its own session (sweep_overdue_turns) must reuse the test's.
    """

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


@pytest.fixture
def registry(monkeypatch):
    """A fresh registry whose started tasks run a no-op instead of a real loop."""

    async def _noop(match_id: str) -> None:
        return None

    monkeypatch.setattr(scheduler, "_run_game", _noop)
    return scheduler.SchedulerRegistry()


@pytest.fixture
def published(monkeypatch):
    events: list[tuple[str, str, dict]] = []

    async def fake_publish(channel: str, event_type: str, payload: dict) -> None:
        events.append((channel, event_type, payload))

    monkeypatch.setattr(scheduler, "publish", fake_publish)
    return events


async def _make_frozen_match(
    db,
    *,
    match_id="G_TEST",
    n_players=3,
    game=None,
    phase="talk",
    talk_resolved=False,
    deadline_offset=-120.0,
    pointer=(1, 1),
):
    """An ACTIVE match with an unresolved (1,1) turn whose deadline is offset
    seconds from now (negative = overdue). Pointer defaults to the turn."""
    now = datetime.now(timezone.utc)
    match = Match(
        id=match_id,
        name="t",
        state=GameState.ACTIVE,
        scheduled_start=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=10),
        per_turn_deadline_seconds=60,
        current_round=pointer[0],
        current_turn=pointer[1],
    )
    if game is not None:
        match.game = game
    db.add(match)
    await db.flush()
    players = []
    for i in range(n_players):
        u = User(google_sub=f"{match_id}-s{i}", email=f"{match_id}-u{i}@t.com")
        db.add(u)
        await db.flush()
        agent, _ = await make_bot(db, u, name=f"AI_{match_id}_{i}")
        p = Player(match_id=match.id, user_id=u.id, agent_id=agent.id, seat_name=f"AI_{i}")
        db.add(p)
        await db.flush()
        players.append(p)
    turn = Turn(
        match_id=match.id,
        round=1,
        turn=1,
        turn_token=f"tk_{match_id}",
        opened_at=now - timedelta(minutes=5),
        deadline_at=now + timedelta(seconds=deadline_offset),
        phase=phase,
        talk_resolved_at=now - timedelta(minutes=4) if talk_resolved else None,
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)
    return match, players, turn


# --- heals ---


async def test_talk_stuck_defaults_talk_and_reopens_act(db, registry, published):
    match, players, turn = await _make_frozen_match(db)

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )
    await asyncio.sleep(0)  # drain the restarted no-op loop task

    assert healed == 1
    await db.refresh(turn)
    assert turn.talk_resolved_at is not None
    assert turn.resolved_at is None  # act phase reopened, not force-resolved
    assert turn.phase == "act"
    # A fresh, full act window — live players still get their fair turn.
    assert ensure_aware(turn.deadline_at) > datetime.now(timezone.utc)
    messages = (
        (await db.execute(select(TurnMessage).where(TurnMessage.turn_id == turn.id)))
        .scalars()
        .all()
    )
    assert {m.player_id for m in messages} == {p.id for p in players}
    assert all(m.was_defaulted for m in messages)
    assert [(c, e) for c, e, _ in published] == [(match.id, "turn_talked")]


async def test_act_stuck_defaults_hoard_and_resolves(db, registry, published):
    match, players, turn = await _make_frozen_match(db, phase="act", talk_resolved=True)

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )
    await asyncio.sleep(0)

    assert healed == 1
    await db.refresh(turn)
    assert turn.resolved_at is not None
    subs = (
        (
            await db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        )
        .scalars()
        .all()
    )
    assert {s.player_id for s in subs} == {p.id for p in players}
    assert all(s.action == "HOARD" and s.was_defaulted for s in subs)
    assert [(c, e) for c, e, _ in published] == [(match.id, "turn_resolved")]


async def test_act_stuck_with_stale_talk_phase_normalizes_then_resolves(
    db, registry, published
):
    # Crash landed between talk resolution and the phase flip: talk_resolved_at
    # is set but phase is still "talk". The heal must normalize to act, then resolve.
    match, _players, turn = await _make_frozen_match(db, phase="talk", talk_resolved=True)

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )
    await asyncio.sleep(0)

    assert healed == 1
    await db.refresh(turn)
    assert turn.phase == "act"
    assert turn.resolved_at is not None
    assert [(c, e) for c, e, _ in published] == [(match.id, "turn_resolved")]


async def test_heal_stops_loop_task_then_restarts_it(db, registry, published, monkeypatch):
    match, _players, _turn = await _make_frozen_match(db, phase="act", talk_resolved=True)
    calls: list[str] = []
    monkeypatch.setattr(registry, "stop", lambda gid: calls.append(f"stop:{gid}"))
    monkeypatch.setattr(registry, "start", lambda gid: calls.append(f"start:{gid}"))

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )

    assert healed == 1
    assert calls == [f"stop:{match.id}", f"start:{match.id}"]


async def test_second_sweep_is_noop(db, registry, published):
    _match, players, turn = await _make_frozen_match(db, phase="act", talk_resolved=True)
    factory = _same_session_factory(db)

    assert await sweep_overdue_turns(session_factory=factory, registry=registry) == 1
    assert await sweep_overdue_turns(session_factory=factory, registry=registry) == 0
    await asyncio.sleep(0)

    subs = (
        (
            await db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(subs) == len(players)  # no duplicate defaulted submissions


# --- skips ---


async def test_skips_slow_but_not_frozen_turns(db, registry, published):
    # Deadline still in the future: not overdue at all.
    _m1, _p1, turn_future = await _make_frozen_match(db, match_id="G_FUT", deadline_offset=60)
    # Overdue, but by less than OVERDUE_TURN_GRACE_SECONDS: slow, not frozen.
    _m2, _p2, turn_slow = await _make_frozen_match(db, match_id="G_SLOW", deadline_offset=-10)

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )

    assert healed == 0
    await db.refresh(turn_future)
    await db.refresh(turn_slow)
    assert turn_future.talk_resolved_at is None
    assert turn_slow.talk_resolved_at is None
    assert published == []


async def test_pointer_mismatch_not_swept_and_task_untouched(
    db, registry, published, monkeypatch
):
    _match, _players, turn = await _make_frozen_match(db, pointer=(2, 3))
    calls: list[str] = []
    monkeypatch.setattr(registry, "stop", lambda gid: calls.append(f"stop:{gid}"))
    monkeypatch.setattr(registry, "start", lambda gid: calls.append(f"start:{gid}"))

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )

    assert healed == 0
    assert calls == []  # anomaly is surfaced, the running loop is left alone
    await db.refresh(turn)
    assert turn.talk_resolved_at is None
    assert turn.resolved_at is None
    assert published == []


async def test_sequential_game_not_swept(db, registry, published):
    _match, _players, turn = await _make_frozen_match(db, game="liars_dice")

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )
    await asyncio.sleep(0)

    assert healed == 0
    await db.refresh(turn)
    assert turn.talk_resolved_at is None
    assert turn.resolved_at is None
    assert published == []


async def test_match_no_longer_active_at_heal_time_is_noop(
    db, registry, published, monkeypatch
):
    # Simulate the loop finishing the match between detection and heal: the
    # fresh-session re-check must bail, but the task must still be restarted.
    match, _players, turn = await _make_frozen_match(db)
    match.state = GameState.COMPLETED
    await db.commit()
    calls: list[str] = []
    monkeypatch.setattr(registry, "stop", lambda gid: calls.append(f"stop:{gid}"))
    monkeypatch.setattr(registry, "start", lambda gid: calls.append(f"start:{gid}"))

    healed = await overdue_sweeper._heal_match(
        _same_session_factory(db), registry, match.id, 1, 1
    )

    assert healed == 0
    assert calls == [f"stop:{match.id}", f"start:{match.id}"]
    await db.refresh(turn)
    assert turn.resolved_at is None
    assert published == []


# --- failure isolation ---


async def test_one_matchs_failure_does_not_strand_others(
    db, registry, published, monkeypatch
):
    _bad, _bp, turn_bad = await _make_frozen_match(db, match_id="G_A")
    _good, _gp, turn_good = await _make_frozen_match(db, match_id="G_B")

    orig_begin = overdue_sweeper._begin_act_phase

    async def begin_or_boom(session, match, turn):
        if match.id == "G_A":
            raise RuntimeError("kaboom")
        await orig_begin(session, match, turn)

    incidents: list[dict] = []

    async def fake_incident(**kwargs) -> None:
        incidents.append(kwargs)

    monkeypatch.setattr(overdue_sweeper, "_begin_act_phase", begin_or_boom)
    monkeypatch.setattr(overdue_sweeper, "record_background_incident", fake_incident)

    healed = await sweep_overdue_turns(
        session_factory=_same_session_factory(db), registry=registry
    )
    await asyncio.sleep(0)

    assert healed == 1
    await db.refresh(turn_good)
    assert turn_good.talk_resolved_at is not None
    assert turn_good.phase == "act"
    assert len(incidents) == 1
    assert incidents[0]["match_id"] == "G_A"
    assert incidents[0]["source"] == "scheduler:sweep_overdue_turns"
    assert incidents[0]["stage"] == "overdue_sweep"
