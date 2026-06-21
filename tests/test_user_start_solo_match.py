"""Tests for the solo-owner "Start now" flow.

When the signed-in viewer is the only person with a human/agent seat in a normal
pre-start match, they may start it immediately; bots fill the table up to the
start floor so the match can run. Covered here: the eligibility rule
(`viewer_start_eligibility`), the bot-fill helper (`fill_match_with_bots`), the
POST `/start` route, and the button's presence on the viewer page.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.arena import fill_match_with_bots
from app.engine.user_match_start import viewer_start_eligibility
from app.main import app
from app.models import Base
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from tests.factories import make_user, seat_player


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _make_match(
    db,
    match_id: str,
    *,
    state: GameState = GameState.REGISTERING,
    kind: MatchKind = MatchKind.MANUAL,
    max_players: int = 20,
) -> Match:
    match = Match(
        id=match_id,
        name=match_id,
        game="hoard-hurt-help",
        state=state,
        scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        per_turn_deadline_seconds=60,
        max_players=max_players,
        match_kind=kind.value,
    )
    db.add(match)
    await db.flush()
    return match


async def _confirmed_player_count(db, match_id: str) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Player)
            .where(
                Player.match_id == match_id,
                Player.left_at.is_(None),
                Player.seat_reserved_until.is_(None),
            )
        )
    ) or 0


async def _bot_count(db, match_id: str) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Player)
            .join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id == match_id, Agent.kind == AgentKind.BOT)
        )
    ) or 0


# --- eligibility ----------------------------------------------------------


@pytest.mark.asyncio
async def test_sole_owner_one_seat_can_start_and_needs_two_bots(reset_db):
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_SOLO1")
        await seat_player(db, match.id, "My Agent", user=user)
        await db.commit()

        elig = await viewer_start_eligibility(db, match, user)
        assert elig.can_start is True
        assert elig.bots_to_add == 2  # 1 player + 2 bots = the 3-player floor


@pytest.mark.asyncio
async def test_sole_owner_with_three_seats_needs_no_bots(reset_db):
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_SOLO3")
        for n in range(3):
            await seat_player(db, match.id, f"Agent {n}", user=user)
        await db.commit()

        elig = await viewer_start_eligibility(db, match, user)
        assert elig.can_start is True
        assert elig.bots_to_add == 0


@pytest.mark.asyncio
async def test_seat_plus_existing_bots_counts_toward_floor(reset_db):
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_BOTS")
        await seat_player(db, match.id, "My Agent", user=user)
        await db.commit()
        await fill_match_with_bots(db, match, 3)  # tops the table up to 3

        elig = await viewer_start_eligibility(db, match, user)
        assert elig.can_start is True
        assert elig.bots_to_add == 0  # already at the floor, no more needed


@pytest.mark.asyncio
async def test_another_user_with_a_seat_blocks_start(reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 1)
        other = await make_user(db, 2)
        match = await _make_match(db, "M_TWO")
        await seat_player(db, match.id, "Mine", user=owner)
        await seat_player(db, match.id, "Theirs", user=other)
        await db.commit()

        assert (await viewer_start_eligibility(db, match, owner)).can_start is False
        assert (await viewer_start_eligibility(db, match, other)).can_start is False


@pytest.mark.asyncio
async def test_viewer_without_a_seat_cannot_start(reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 1)
        bystander = await make_user(db, 2)
        match = await _make_match(db, "M_BY")
        await seat_player(db, match.id, "Mine", user=owner)
        await db.commit()

        assert (await viewer_start_eligibility(db, match, bystander)).can_start is False
        assert (await viewer_start_eligibility(db, match, None)).can_start is False


@pytest.mark.asyncio
async def test_active_match_cannot_be_started(reset_db):
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_ACT", state=GameState.ACTIVE)
        await seat_player(db, match.id, "Mine", user=user)
        await db.commit()

        assert (await viewer_start_eligibility(db, match, user)).can_start is False


@pytest.mark.asyncio
async def test_auto_match_is_user_startable_when_solo(reset_db):
    # The reported gap: a solo player in an auto-scheduled match saw no button.
    # Match kind must not matter — only "am I the only one here".
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_AUTO", kind=MatchKind.AUTO_SCHEDULED)
        await seat_player(db, match.id, "Mine", user=user)
        await db.commit()

        elig = await viewer_start_eligibility(db, match, user)
        assert elig.can_start is True
        assert elig.bots_to_add == 2


@pytest.mark.asyncio
async def test_only_a_held_seat_cannot_start(reset_db):
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_HELD")
        player = await seat_player(db, match.id, "Pending", user=user)
        player.seat_reserved_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        await db.commit()

        assert (await viewer_start_eligibility(db, match, user)).can_start is False


# --- the route ------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_route_fills_bots_and_activates(client, reset_db, monkeypatch):
    from app.engine import scheduler

    # Don't spin up a real turn-loop task — we only assert the DB transition.
    monkeypatch.setattr(scheduler.registry, "start", lambda gid: None)

    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_GO")
        await seat_player(db, match.id, "My Agent", user=user)
        await db.commit()
        user_id = user.id

    r = await client.post(
        "/games/hoard-hurt-help/matches/M_GO/start",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/M_GO"

    async with reset_db() as db:
        match = await db.get(Match, "M_GO")
        assert match.state == GameState.ACTIVE
        assert await _confirmed_player_count(db, "M_GO") == 3
        assert await _bot_count(db, "M_GO") == 2


@pytest.mark.asyncio
async def test_human_can_start_auto_match_end_to_end(client, reset_db, monkeypatch):
    # The exact reported scenario: join an auto-match as a human, then start it.
    from app.engine import scheduler

    monkeypatch.setattr(scheduler.registry, "start", lambda gid: None)

    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_AUTOH", kind=MatchKind.AUTO_SCHEDULED)
        await db.commit()
        user_id = user.id

    join = await client.post(
        "/games/hoard-hurt-help/matches/M_AUTOH/play/join",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert join.status_code == 303

    start = await client.post(
        "/games/hoard-hurt-help/matches/M_AUTOH/start",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert start.status_code == 303

    async with reset_db() as db:
        match = await db.get(Match, "M_AUTOH")
        assert match.state == GameState.ACTIVE
        assert await _confirmed_player_count(db, "M_AUTOH") == 3  # human + 2 bots


@pytest.mark.asyncio
async def test_start_route_rejects_a_stranger(client, reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 1)
        stranger = await make_user(db, 2)
        match = await _make_match(db, "M_NO")
        await seat_player(db, match.id, "Mine", user=owner)
        await db.commit()
        stranger_id = stranger.id

    r = await client.post(
        "/games/hoard-hurt-help/matches/M_NO/start",
        cookies=_cookies(stranger_id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "CANNOT_START"

    async with reset_db() as db:
        assert (await db.get(Match, "M_NO")).state == GameState.REGISTERING


@pytest.mark.asyncio
async def test_start_route_rejects_when_another_user_is_in(client, reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 1)
        other = await make_user(db, 2)
        match = await _make_match(db, "M_SHARED")
        await seat_player(db, match.id, "Mine", user=owner)
        await seat_player(db, match.id, "Theirs", user=other)
        await db.commit()
        owner_id = owner.id

    r = await client.post(
        "/games/hoard-hurt-help/matches/M_SHARED/start",
        cookies=_cookies(owner_id),
        follow_redirects=False,
    )
    assert r.status_code == 409


# --- the button -----------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_page_shows_start_button_for_sole_owner(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await _make_match(db, "M_BTN")
        await seat_player(db, match.id, "My Agent", user=user)
        await db.commit()
        user_id = user.id

    r = await client.get(
        "/games/hoard-hurt-help/matches/M_BTN", cookies=_cookies(user_id)
    )
    assert r.status_code == 200
    assert "Start now" in r.text
    assert 'action="/games/hoard-hurt-help/matches/M_BTN/start"' in r.text


@pytest.mark.asyncio
async def test_viewer_page_hides_start_button_from_stranger(client, reset_db):
    async with reset_db() as db:
        owner = await make_user(db, 1)
        stranger = await make_user(db, 2)
        match = await _make_match(db, "M_BTN2")
        await seat_player(db, match.id, "Mine", user=owner)
        await db.commit()
        stranger_id = stranger.id

    r = await client.get(
        "/games/hoard-hurt-help/matches/M_BTN2", cookies=_cookies(stranger_id)
    )
    assert r.status_code == 200
    assert "Start now" not in r.text
