"""Slice 2 — human join (no setup) and leave (pre-start free / in-match autopilot)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.main import app
from app.models import Base, GameState, Match, Player
from app.models.agent import Agent, AgentKind
from tests.factories import make_user

GAME = "hoard-hurt-help"


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


async def _make_match(db, match_id: str, *, state: GameState, max_players: int = 20) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game=GAME,
        state=state,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        max_players=max_players,
    )
    db.add(match)
    await db.flush()
    return match


async def test_join_creates_active_human_seat(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)  # handle "agent1"
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/games/{GAME}/matches/M_0001"

    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        assert len(players) == 1
        p = players[0]
        assert p.user_id == user.id
        assert p.seat_name == "agent1"
        assert p.seat_reserved_until is None  # active immediately, never held
        assert p.left_at is None
        agent = (
            await db.execute(select(Agent).where(Agent.id == p.agent_id))
        ).scalar_one()
        assert agent.kind == AgentKind.HUMAN
        assert agent.provider is None


async def test_join_uses_custom_display_name(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        data={"display_name": "Maverick"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.seat_name == "Maverick"


async def test_join_is_idempotent_returns_to_viewer(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    for _ in range(2):
        r = await client.post(
            f"/games/{GAME}/matches/M_0001/play/join",
            cookies=_cookies(user.id),
            follow_redirects=False,
        )
        assert r.status_code == 303

    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        assert len(players) == 1  # no duplicate seat


async def test_join_refused_when_full(reset_db, client) -> None:
    async with reset_db() as db:
        owner = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING, max_players=1)
        # seat one other player to fill it
        other = await make_user(db, 2)
        filler_agent = Agent(user_id=other.id, name="filler", kind=AgentKind.HUMAN, game=GAME)
        db.add(filler_agent)
        await db.flush()
        db.add(Player(match_id="M_0001", user_id=other.id, agent_id=filler_agent.id, seat_name="bob"))
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(owner.id),
        follow_redirects=False,
    )
    assert r.status_code == 409


async def test_join_refused_when_not_open(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.ACTIVE)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409


async def test_join_requires_sign_in(reset_db, client) -> None:
    async with reset_db() as db:
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join", follow_redirects=False
    )
    assert r.status_code == 401


async def test_pre_start_leave_frees_seat(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()
    await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/leave",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.left_at is not None  # seat freed
        assert p.autopilot_at is None


async def test_in_match_leave_sets_autopilot(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()
    await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    # match starts
    async with reset_db() as db:
        match = (await db.execute(select(Match))).scalar_one()
        match.state = GameState.ACTIVE
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/leave",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.left_at is None  # still seated / ranked
        assert p.autopilot_at is not None  # auto-Hoards to the end
