"""Tests for the live connection handshake (specs/005-bot-onboarding-handshake).

Covers the onboarding state machine, first-connection detection (via the agent
auth choke point), first-move detection, owner-scoping of the new routes, and
correct first paint.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.engine.bot_activity import (
    OnboardingState,
    bot_channel,
    compute_onboarding_status,
    mark_first_move,
    mark_seen,
)
from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Base, Bot, Game, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot, make_user, seat_player

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr("app.routes.agent_api._last_poll", {})
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def events(monkeypatch):
    """Capture every broadcast.publish call as (channel, event_type, payload)."""
    captured: list[tuple[str, str, dict]] = []

    async def fake_publish(channel: str, event_type: str, payload: dict) -> None:
        captured.append((channel, event_type, payload))

    monkeypatch.setattr("app.broadcast.publish", fake_publish)
    return captured


def _signed_in_cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _game(db, gid: str, state: GameState) -> Game:
    g = Game(
        id=gid,
        name=f"Game {gid}",
        state=state,
        scheduled_start=NOW + timedelta(hours=1),
        per_turn_deadline_seconds=60,
    )
    db.add(g)
    await db.flush()
    return g


async def _player(db, game_id: str, bot: Bot, user: User, agent_id: str = "AI_0") -> Player:
    p = Player(game_id=game_id, user_id=user.id, bot_id=bot.id, agent_id=agent_id)
    db.add(p)
    await db.flush()
    return p


async def _submission(
    db, game_id: str, player: Player, *, round_: int, turn_: int, defaulted: bool = False
) -> TurnSubmission:
    t = Turn(
        game_id=game_id,
        round=round_,
        turn=turn_,
        turn_token=generate_turn_token(),
        opened_at=NOW,
        deadline_at=NOW + timedelta(minutes=1),
    )
    db.add(t)
    await db.flush()
    s = TurnSubmission(
        turn_id=t.id,
        player_id=player.id,
        action="HELP",
        was_defaulted=defaulted,
        submitted_at=NOW,
    )
    db.add(s)
    await db.flush()
    return s


# --------------------------------------------------------------------------
# Foundation: the onboarding state machine (T004 / T006)
# --------------------------------------------------------------------------


async def test_state_waiting_when_never_connected_no_games(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.WAITING


async def test_state_waiting_in_game_when_entered_but_not_connected(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        g = await _game(db, "G_1", GameState.REGISTERING)
        await _player(db, g.id, bot, u)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.WAITING_IN_GAME
    assert status.game_name == "Game G_1"


async def test_state_connected_no_game(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.CONNECTED_NO_GAME


async def test_state_connected_pregame(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW
        g = await _game(db, "G_1", GameState.REGISTERING)
        await _player(db, g.id, bot, u)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.CONNECTED_PREGAME


async def test_state_in_game_no_move(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW
        g = await _game(db, "G_1", GameState.ACTIVE)
        await _player(db, g.id, bot, u)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.IN_GAME_NO_MOVE
    assert status.game_id == "G_1"


async def test_state_playing_when_moved(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, bot, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    # Play history wins even though first_connected_at is NULL (back-compat).
    assert status.state is OnboardingState.PLAYING
    assert status.game_id == "G_1"


async def test_playing_points_only_at_a_live_game_not_a_finished_one(reset_db):
    # Regression: a bot that played a real move in a game that has since finished
    # is still "playing" (established), but must NOT carry a game link — pointing
    # "Watch live" at a completed game is a dead link.
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        g = await _game(db, "G_1", GameState.COMPLETED)
        p = await _player(db, g.id, bot, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.PLAYING
    assert status.game_id is None
    assert status.game_name is None


async def test_defaulted_submission_does_not_count_as_moved(reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, bot, u)
        await _submission(db, g.id, p, round_=1, turn_=1, defaulted=True)
        await db.commit()
        status = await compute_onboarding_status(db, bot)
    assert status.state is OnboardingState.IN_GAME_NO_MOVE


# --------------------------------------------------------------------------
# Foundation: signal emission (T005)
# --------------------------------------------------------------------------


async def test_mark_seen_sets_and_publishes_once(reset_db, events):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        await db.commit()

        await mark_seen(db, bot, key_hash=bot.key_lookup)
        first = bot.first_connected_at
        await mark_seen(db, bot, key_hash=bot.key_lookup)  # no second 'connected'

    assert first is not None
    assert bot.first_connected_at == first  # unchanged on second call
    assert bot.last_seen_at is not None  # heartbeat stamped on connect
    connected = [e for e in events if e[1] == "connected"]
    assert connected == [(bot_channel(bot.id), "connected", {})]


async def test_mark_first_move_publishes_only_on_first(reset_db, events):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        g = await _game(db, "G_1", GameState.ACTIVE)
        p = await _player(db, g.id, bot, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()

        await mark_first_move(db, bot.id)  # exactly one real submission → publish

        await _submission(db, g.id, p, round_=1, turn_=2)
        await db.commit()
        await mark_first_move(db, bot.id)  # now two → no publish

    moved = [e for e in events if e[1] == "moved"]
    assert moved == [(bot_channel(bot.id), "moved", {})]


# --------------------------------------------------------------------------
# US1: first connection detected through the agent auth choke point (T012)
# --------------------------------------------------------------------------


async def test_agent_call_records_connection_once(client, reset_db, events):
    async with reset_db() as db:
        await _game(db, "G_1", GameState.REGISTERING)
        p = await seat_player(db, "G_1", "AI_0", i=0)
        await db.commit()
        bot_id, key = p.bot_id, p._test_key

    r = await client.get("/api/games/G_1/turn", headers={"X-Agent-Key": key})
    assert r.status_code == 200

    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
        assert bot.first_connected_at is not None

    # A second call must not re-publish (idempotent).
    await client.get("/api/games/G_1/turn", headers={"X-Agent-Key": key})
    connected = [e for e in events if e[1] == "connected"]
    assert connected == [(bot_channel(bot_id), "connected", {})]


# --------------------------------------------------------------------------
# US1/US2: the status fragment — owner-scoping, first paint, join guidance
# --------------------------------------------------------------------------


async def test_status_fragment_first_paint_waiting(client, reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        await db.commit()
        uid, bid = u.id, bot.id

    r = await client.get(f"/me/bots/{bid}/status", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    assert "Waiting for your bot to connect" in r.text


async def test_status_fragment_connected_no_game_shows_join(client, reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW
        await db.commit()
        uid, bid = u.id, bot.id

    r = await client.get(f"/me/bots/{bid}/status", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    assert "Find a game to join" in r.text


async def test_detail_empty_games_copy_when_connected(client, reset_db):
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW
        await db.commit()
        uid, bid = u.id, bot.id

    r = await client.get(f"/me/bots/{bid}", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    assert "Connected but not in a game yet" in r.text


async def test_detail_established_bot_shows_only_badge_not_a_playing_line(client, reset_db):
    # The bug this fixes: a bot that played a real move, whose game has since
    # ended and whose runner is now offline, showed BOTH "Playing in <game>" (from
    # play history) AND a "Disconnected" badge (from a cold heartbeat) at once.
    # The onboarding panel must not render for an established bot — the health
    # badge is the single source of truth.
    async with reset_db() as db:
        u = await make_user(db)
        bot, _ = await make_bot(db, u)
        bot.first_connected_at = NOW  # connected once, long ago (NOW is in the past)
        g = await _game(db, "G_1", GameState.COMPLETED)
        p = await _player(db, g.id, bot, u)
        await _submission(db, g.id, p, round_=1, turn_=1)
        await db.commit()
        uid, bid = u.id, bot.id

    r = await client.get(f"/me/bots/{bid}", cookies=_signed_in_cookies(uid))
    assert r.status_code == 200
    # Badge tells the truth — the runner is offline.
    assert "Disconnected" in r.text
    # No contradicting onboarding panel / "playing" line.
    assert 'id="bot-status-live"' not in r.text
    assert "it's playing now" not in r.text


async def test_status_fragment_owner_only(client, reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 0)
        other = await make_user(db, 1)
        bot, _ = await make_bot(db, owner)
        await db.commit()
        bid, other_id = bot.id, other.id

    r = await client.get(f"/me/bots/{bid}/status", cookies=_signed_in_cookies(other_id))
    assert r.status_code == 404


async def test_stream_rejects_non_owner(client, reset_db):
    # The security guarantee (FR-010): a non-owner cannot open the bot's stream.
    # `_owned_bot` 404s in the dependency, before any streaming begins. The
    # owner's 200 path is an infinite event-stream that ASGITransport can't
    # cleanly consume in-process, so it's exercised in the live preview instead
    # (quickstart), not here.
    async with reset_db() as db:
        owner = await make_user(db, 0)
        other = await make_user(db, 1)
        bot, _ = await make_bot(db, owner)
        await db.commit()
        bid, other_id = bot.id, other.id

    r = await client.get(f"/me/bots/{bid}/stream", cookies=_signed_in_cookies(other_id))
    assert r.status_code == 404
