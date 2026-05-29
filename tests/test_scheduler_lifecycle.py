"""Scheduler lifecycle tests: resolve-early and auto-start of due games."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import scheduler
from app.engine.scheduler import _all_submitted, _wait_for_turn
from app.engine.tokens import generate_turn_token
from app.models import Base, Game, GameState, Player, Turn, TurnSubmission, User


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
    game = Game(
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
        p = Player(game_id=game.id, user_id=u.id, agent_id=f"AI_{i}", agent_key_hash="h")
        db.add(p)
        await db.flush()
        players.append(p)
    await db.commit()
    return game, players


async def _open_turn(db, game, deadline_secs=60):
    now = datetime.now(timezone.utc)
    turn = Turn(
        game_id=game.id,
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
