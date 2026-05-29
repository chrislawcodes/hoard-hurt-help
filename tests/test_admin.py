"""Admin auth + game creation + export tests."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Base, Game, GameState, Player, StrategyPrompt, Turn, TurnSubmission, User


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
    monkeypatch.setattr(settings, "admin_emails", "admin@test.com")

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


async def _seed_user(reset_db, email: str) -> User:
    async with reset_db() as db:
        u = User(google_sub=f"sub-{email}", email=email, name=email)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


@pytest.mark.asyncio
async def test_non_admin_blocked(client, reset_db):
    user = await _seed_user(reset_db, "regular@test.com")
    r = await client.get("/admin", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_see_dashboard(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    r = await client.get("/admin", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Admin Dashboard" in r.text


@pytest.mark.asyncio
async def test_admin_creates_game_via_api(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/games",
        json={
            "name": "QA",
            "scheduled_start": when,
            "min_players": 3,
            "max_players": 10,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("G_")
    assert body["state"] == "registering"


@pytest.mark.asyncio
async def test_create_game_via_web_form(client, reset_db):
    """The browser posts a UTC ISO string (from datetime-local JS conversion)."""
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/admin/games/new",
        data={
            "name": "Web Night",
            "scheduled_start": future,
            "min_players": "3",
            "max_players": "10",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303  # redirect on success


@pytest.mark.asyncio
async def test_web_form_rejects_past_time(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/admin/games/new",
        data={
            "name": "Past",
            "scheduled_start": past,
            "min_players": "3",
            "max_players": "10",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "must be in the future" in r.text


@pytest.mark.asyncio
async def test_admin_cancel_pre_start(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        g = Game(
            id="G_001",
            name="t",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.commit()
    r = await client.post(
        "/api/admin/games/G_001/cancel", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_delete_game_removes_everything(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="pu", email="pu@test.com")
        db.add(u)
        await db.flush()
        g = Game(
            id="G_001",
            name="Doomed",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.flush()
        p = Player(game_id="G_001", user_id=u.id, agent_id="AI_0", agent_key_hash="x")
        db.add(p)
        await db.flush()
        db.add(StrategyPrompt(player_id=p.id, prompt_text="plan", is_default=False))
        await db.commit()

    r = await client.post(
        "/admin/games/G_001/delete",
        data={"next": "/admin"},
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        gone = (await db.execute(select(Game).where(Game.id == "G_001"))).scalar_one_or_none()
        players = (
            (await db.execute(select(Player).where(Player.game_id == "G_001"))).scalars().all()
        )
    assert gone is None
    assert players == []


@pytest.mark.asyncio
async def test_non_admin_cannot_delete(client, reset_db):
    user = await _seed_user(reset_db, "regular@test.com")
    async with reset_db() as db:
        g = Game(
            id="G_001",
            name="t",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.commit()
    r = await client.post(
        "/admin/games/G_001/delete",
        data={"next": "/admin"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_export_csv_shape(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Game(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        p = Player(
            game_id="G_001",
            user_id=u.id,
            agent_id="AI_0",
            agent_key_hash="x",
        )
        db.add(p)
        await db.flush()
        t = Turn(
            game_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=p.id,
                action="HOARD",
                message="hi",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get(
        "/api/admin/games/G_001/export.csv", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    text = r.text
    header = text.split("\n")[0]
    assert "game_id,round,turn,agent_id,action" in header
    assert "AI_0" in text
    assert "HOARD" in text


@pytest.mark.asyncio
async def test_export_json_includes_strategy_prompts(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Game(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        p = Player(game_id="G_001", user_id=u.id, agent_id="AI_0", agent_key_hash="x")
        db.add(p)
        await db.flush()
        db.add(
            StrategyPrompt(player_id=p.id, prompt_text="secret strategy", is_default=False)
        )
        await db.commit()

    r = await client.get(
        "/api/admin/games/G_001/export.json", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["players"][0]["strategy_prompt"] == "secret strategy"
