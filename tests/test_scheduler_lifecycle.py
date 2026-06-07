"""Scheduler lifecycle tests: resolve-early and auto-start of due games."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.engine import scheduler
from app.engine.scheduler import _all_submitted, _wait_for_turn
from app.engine.tokens import generate_turn_token
from app.models import Base, Match, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot


@pytest.fixture
async def db(engine, session_factory):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session


def _same_session_factory(session):
    """A session_factory shim that hands back an already-open session.

    The in-memory test engine doesn't share one connection across sessions, so
    code that opens its own session (start_due_games) must reuse the test's.
    """

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


async def _make_game(db, *, state=GameState.ACTIVE, n_players=3, start_offset=-5, min_players=3):
    now = datetime.now(timezone.utc)
    game = Match(
        id="G_TEST",
        name="t",
        state=state,
        scheduled_start=now + timedelta(seconds=start_offset),
        per_turn_deadline_seconds=60,
        min_players=min_players,
    )
    db.add(game)
    await db.flush()
    players = []
    for i in range(n_players):
        u = User(google_sub=f"s{i}", email=f"u{i}@t.com")
        db.add(u)
        await db.flush()
        agent, _ = await make_bot(db, u, name=f"AI_{i}")
        p = Player(match_id=game.id, user_id=u.id, agent_id=agent.id, seat_name=f"AI_{i}")
        db.add(p)
        await db.flush()
        players.append(p)
    await db.commit()
    return game, players


async def _open_turn(db, game, deadline_secs=60):
    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id=game.id,
        round=1,
        turn=1,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=deadline_secs),
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)
    return turn


async def _submit(db, turn, player):
    db.add(
        TurnSubmission(
            turn_id=turn.id,
            player_id=player.id,
            action="HOARD",
            target_player_id=None,
            message="",
            submitted_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


# --- resolve-early ---


async def test_all_submitted_false_until_everyone_in(db):
    game, players = await _make_game(db, n_players=3)
    turn = await _open_turn(db, game)
    assert await _all_submitted(db, turn) is False
    await _submit(db, turn, players[0])
    await _submit(db, turn, players[1])
    assert await _all_submitted(db, turn) is False  # 2 of 3
    await _submit(db, turn, players[2])
    assert await _all_submitted(db, turn) is True


async def test_wait_for_turn_returns_early_when_all_submitted(db):
    game, players = await _make_game(db, n_players=3)
    turn = await _open_turn(db, game, deadline_secs=30)  # deadline far away
    for p in players:
        await _submit(db, turn, p)
    # Resolve-early must return well before the 30s deadline; timeout proves it.
    await asyncio.wait_for(_wait_for_turn(db, turn), timeout=5)


# --- auto-start ---


async def test_start_due_games_starts_due_and_full(db, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(scheduler.registry, "start", lambda gid: started.append(gid))
    game, _ = await _make_game(db, state=GameState.REGISTERING, n_players=3, start_offset=-5)

    n = await scheduler.registry.start_due_games(session_factory=_same_session_factory(db))

    assert n == 1
    assert started == [game.id]
    await db.refresh(game)
    assert game.state == GameState.ACTIVE


async def test_start_due_games_skips_not_yet_due(db, monkeypatch):
    monkeypatch.setattr(scheduler.registry, "start", lambda gid: None)
    game, _ = await _make_game(db, state=GameState.REGISTERING, n_players=3, start_offset=300)

    n = await scheduler.registry.start_due_games(session_factory=_same_session_factory(db))

    assert n == 0
    await db.refresh(game)
    assert game.state == GameState.REGISTERING


async def test_start_due_games_cancels_due_game_under_floor(db, monkeypatch):
    # A due game with fewer than the hard floor of players is cancelled, not left
    # stuck in REGISTERING forever.
    monkeypatch.setattr(scheduler.registry, "start", lambda gid: None)
    game, _ = await _make_game(
        db, state=GameState.REGISTERING, n_players=2, start_offset=-5, min_players=3
    )

    n = await scheduler.registry.start_due_games(session_factory=_same_session_factory(db))

    assert n == 0
    await db.refresh(game)
    assert game.state == GameState.CANCELLED


# --- idempotent resume (a mid-game restart must not crash the loop) ---


async def test_open_turn_reuses_existing_row_on_resume(db):
    # Resuming at game.current_turn hits a turn row that already exists.
    # _open_turn must return that same row, not blow up on the
    # (match_id, round, turn) unique constraint (the bug that froze G_0012).
    game, _ = await _make_game(db, n_players=3)
    first = await scheduler._open_turn(db, game, 2, 5)
    again = await scheduler._open_turn(db, game, 2, 5)

    assert again.id == first.id
    count = await db.scalar(
        select(func.count())
        .select_from(Turn)
        .where(Turn.match_id == game.id, Turn.round == 2, Turn.turn == 5)
    )
    assert count == 1


async def test_open_turn_creates_fresh_row_and_sets_pointer(db):
    game, _ = await _make_game(db, n_players=3)
    turn = await scheduler._open_turn(db, game, 3, 7)

    assert (turn.round, turn.turn) == (3, 7)
    assert turn.resolved_at is None
    await db.refresh(game)
    assert (game.current_round, game.current_turn) == (3, 7)


async def test_crashed_game_loop_is_logged(monkeypatch):
    # A crash inside the loop must be surfaced, not swallowed as an unretrieved
    # task exception — that silent swallowing is what froze G_0012 with no log.
    reg = scheduler.SchedulerRegistry()
    logged: list[str] = []

    async def boom(match_id: str) -> None:
        raise RuntimeError("kaboom")

    def record_error(msg: str, *args, **kwargs) -> None:
        logged.append(msg % args if args else msg)

    monkeypatch.setattr(scheduler, "_run_game", boom)
    monkeypatch.setattr(scheduler.logger, "error", record_error)
    reg.start("G_X")
    task = reg._tasks["G_X"]
    with pytest.raises(RuntimeError):
        await task
    assert any("crashed" in message for message in logged)


# --- read-path reconciliation (lobby self-heal) ---


async def test_cancel_overdue_unfilled_cancels_due_underfilled(db):
    # A due game with too few players is cancelled on the caller's own session,
    # so the lobby render that triggered it immediately sees CANCELLED.
    game, _ = await _make_game(
        db, state=GameState.REGISTERING, n_players=0, start_offset=-5
    )

    n = await scheduler.cancel_overdue_unfilled_games(db)

    assert n == 1
    await db.refresh(game)
    assert game.state == GameState.CANCELLED
    assert game.cancelled_at is not None


async def test_cancel_overdue_unfilled_leaves_future_game(db):
    game, _ = await _make_game(
        db, state=GameState.REGISTERING, n_players=0, start_offset=300
    )

    n = await scheduler.cancel_overdue_unfilled_games(db)

    assert n == 0
    await db.refresh(game)
    assert game.state == GameState.REGISTERING


async def test_cancel_overdue_unfilled_leaves_due_full_game(db):
    # Due and at the floor: the poller starts it. The read path must NOT cancel a
    # game that is merely waiting to start, nor spin up its turn loop.
    game, _ = await _make_game(
        db, state=GameState.REGISTERING, n_players=3, start_offset=-5
    )

    n = await scheduler.cancel_overdue_unfilled_games(db)

    assert n == 0
    await db.refresh(game)
    assert game.state == GameState.REGISTERING


async def test_start_due_games_starts_due_scheduled_game(db, monkeypatch):
    # start_due_games sweeps SCHEDULED too; start_game must promote it (SCHEDULED
    # can't transition straight to ACTIVE) rather than throw.
    started: list[str] = []
    monkeypatch.setattr(scheduler.registry, "start", lambda gid: started.append(gid))
    game, _ = await _make_game(db, state=GameState.SCHEDULED, n_players=3, start_offset=-5)

    n = await scheduler.registry.start_due_games(session_factory=_same_session_factory(db))

    assert n == 1
    assert started == [game.id]
    await db.refresh(game)
    assert game.state == GameState.ACTIVE
