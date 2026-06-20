"""Slice 3 — the play panel renders for the seated human only, with the right state."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.human_player import get_or_create_human_agent
from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Base, GameState, Match, Player, User
from app.models.turn import Turn
from tests.factories import make_user, seat_player

GAME = "hoard-hurt-help"
VIEWER = f"/games/{GAME}/matches/M_0001"
LIVE = f"{VIEWER}/live"


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _match(db, *, state: GameState) -> Match:
    match = Match(
        id="M_0001",
        name="Match M_0001",
        game=GAME,
        state=state,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        max_players=20,
    )
    db.add(match)
    await db.flush()
    return match


async def _seat_human(db, user: User, seat_name: str) -> Player:
    agent, version = await get_or_create_human_agent(db, user, GAME)
    player = Player(
        match_id="M_0001",
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
    )
    db.add(player)
    await db.flush()
    return player


async def _open_turn(db, phase: str) -> Turn:
    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id="M_0001",
        round=1,
        turn=1,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
        phase=phase,
    )
    db.add(turn)
    await db.flush()
    return turn


async def test_panel_renders_for_seated_human_on_act_turn(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await _open_turn(db, "act")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert r.status_code == 200
    html = r.text
    assert 'id="play-panel"' in html
    assert 'data-your-turn="act"' in html
    assert "Lock in my move" in html
    assert "+4 them" in html  # payoff hint
    assert "+8 mutual" in html  # the cooperation upside lives on the Help card
    assert "bob" in html  # target option present


async def test_spectator_sees_no_panel_but_sees_waiting(reset_db, client) -> None:
    async with reset_db() as db:
        human = await make_user(db, 1)
        spectator = await make_user(db, 9)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, human, "alice")
        await _open_turn(db, "act")
        await db.commit()
        spectator_id = spectator.id

    r = await client.get(LIVE, cookies=_cookies(spectator_id))
    assert r.status_code == 200
    assert 'id="play-panel"' not in r.text
    assert "Waiting on" in r.text  # additive pace indicator visible to all


async def test_panel_shows_submitted_state(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await _open_turn(db, "act")
        await db.commit()

    # submit, then re-fetch the live fragment
    await client.post(
        f"{VIEWER}/play/act", data={"action": "HOARD"}, cookies=_cookies(user.id)
    )
    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "Submitted — you can still change this" in r.text


async def test_talk_panel_has_pass(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await _open_turn(db, "talk")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "data-play-pass" in r.text
    assert "say something" in r.text
    assert "data-play-counter" in r.text  # character counter wired up


async def test_act_panel_reveals_this_turns_talk(reset_db, client) -> None:
    """During act, the human sees what others said this turn — speakers and the
    silent — since the open turn isn't in the feed yet."""
    from app.models.turn import TurnMessage

    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        human = await _seat_human(db, user, "alice")
        bob = await seat_player(db, "M_0001", "bob", i=2)
        await seat_player(db, "M_0001", "cy", i=3)  # stays silent this turn
        turn = await _open_turn(db, "act")
        # bob spoke this turn; cy stayed silent; the human's own note is hidden.
        db.add(TurnMessage(turn_id=turn.id, player_id=bob.id, text="let's both help"))
        db.add(TurnMessage(turn_id=turn.id, player_id=human.id, text="my private note"))
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    html = r.text
    assert "What was just said" in html
    assert "let&#39;s both help" in html or "let's both help" in html
    assert "bob" in html
    assert "cy stayed quiet" in html
    assert "my private note" not in html  # the viewer's own message isn't echoed


async def test_join_cta_on_scheduled_viewer(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(VIEWER, cookies=_cookies(user.id))
    assert r.status_code == 200
    assert "Play this match" in r.text


async def test_autopilot_panel_shows_left_state(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        player = await _seat_human(db, user, "alice")
        player.autopilot_at = datetime.now(timezone.utc)
        await _open_turn(db, "act")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "You left this match" in r.text
    assert "Lock in my move" not in r.text  # no active form when on autopilot
