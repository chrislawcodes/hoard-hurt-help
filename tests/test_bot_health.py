"""Tests for compute_bot_health — the operational-health badge state machine.

Green (Live/Ready) only when the runner is actually alive (warm heartbeat); red
(Stalled/Disconnected) when it's down or failing; grey (Paused) on owner intent.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.bot_activity import BotHealth, compute_bot_health
from app.engine.tokens import generate_turn_token
from app.models import Base, Match, GameState, Player, Turn, TurnSubmission
from app.models.bot import BotStatus
from tests.factories import make_bot, make_user

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
COLD = NOW - timedelta(minutes=10)  # well past the 90s live window
WARM = NOW - timedelta(seconds=20)  # inside the live window


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    yield test_factory
    await test_engine.dispose()


async def _game(db, gid: str, state: GameState) -> Match:
    g = Match(
        id=gid,
        name=f"Match {gid}",
        state=state,
        scheduled_start=NOW + timedelta(hours=1),
        per_turn_deadline_seconds=60,
    )
    db.add(g)
    await db.flush()
    return g


async def _seat(db, game: Match, bot, user, agent_id: str = "A") -> Player:
    p = Player(match_id=game.id, user_id=user.id, bot_id=bot.id, agent_id=agent_id)
    db.add(p)
    await db.flush()
    return p


async def _submit(db, match_id: str, player: Player, turn_: int, defaulted: bool) -> None:
    t = Turn(
        match_id=match_id,
        round=1,
        turn=turn_,
        turn_token=generate_turn_token(),
        opened_at=NOW,
        deadline_at=NOW + timedelta(seconds=60),
    )
    db.add(t)
    await db.flush()
    db.add(
        TurnSubmission(
            turn_id=t.id, player_id=player.id, action="HOARD", was_defaulted=defaulted
        )
    )
    await db.flush()


async def test_paused_overrides_everything(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.status = BotStatus.PAUSED
        bot.last_seen_at = WARM  # warm, but paused wins
        g = await _game(db, "G_1", GameState.ACTIVE)
        await _seat(db, g, bot, u)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.PAUSED
    assert h.needs_reconnect is False


async def test_live_when_warm_and_in_active_game(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.last_seen_at = WARM
        g = await _game(db, "G_1", GameState.ACTIVE)
        await _seat(db, g, bot, u)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.LIVE
    assert h.needs_reconnect is False
    assert h.match_id == "G_1"
    assert h.badge_class == "badge-ok"


async def test_ready_when_warm_and_no_active_game(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.last_seen_at = WARM
        # In a pre-game lobby, not an active game → still Ready (alive, nothing to play).
        g = await _game(db, "G_1", GameState.REGISTERING)
        await _seat(db, g, bot, u)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.READY
    assert h.badge_class == "badge-ok"
    assert h.needs_reconnect is False


async def test_disconnected_when_never_connected(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)  # last_seen + first_connected both NULL
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.DISCONNECTED
    assert h.never_connected is True
    assert h.last_connected_human is None
    assert h.needs_reconnect is True


async def test_disconnected_when_cold_and_no_game(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.last_seen_at = COLD
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.DISCONNECTED
    assert h.never_connected is False
    assert h.last_connected_human == "10m ago"


async def test_stalled_when_cold_in_active_game(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.last_seen_at = COLD
        g = await _game(db, "G_1", GameState.ACTIVE)
        await _seat(db, g, bot, u)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.STALLED
    assert h.needs_reconnect is True
    assert h.match_id == "G_1"
    assert h.badge_class == "badge-alert"


async def test_stalled_when_warm_but_defaulting(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.last_seen_at = WARM  # runner is alive...
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _seat(db, g, bot, u)
        # ...but its last stall_threshold (3) moves all defaulted → failing.
        for turn_ in (1, 2, 3):
            await _submit(db, g.id, p, turn_, defaulted=True)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.STALLED
    assert h.needs_reconnect is True


async def test_live_when_warm_and_latest_move_is_real(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.last_seen_at = WARM
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _seat(db, g, bot, u)
        # Older defaults, but the most recent move is real → not stalled.
        await _submit(db, g.id, p, 1, defaulted=True)
        await _submit(db, g.id, p, 2, defaulted=True)
        await _submit(db, g.id, p, 3, defaulted=False)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.LIVE


async def test_last_connected_falls_back_to_first_connected(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        # Connected once before the heartbeat column existed: first set, last NULL.
        bot.first_connected_at = NOW - timedelta(minutes=5)
        await db.commit()

        h = await compute_bot_health(db, bot, now=NOW)
    assert h.state is BotHealth.DISCONNECTED  # last_seen is cold/NULL
    assert h.never_connected is False
    assert h.last_connected_human == "5m ago"
