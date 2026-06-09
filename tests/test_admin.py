"""Admin auth + game creation + export tests."""

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from starlette.requests import Request

from app.config import settings
from app.main import app
from app.models import Base, Match, GameState, Player, Turn, TurnSubmission, User
from app.routes import admin_web
from app.routes import game_admin_web
from tests.factories import make_agent


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
    assert "Platform dashboard" in r.text


@pytest.mark.asyncio
async def test_admin_creates_game_via_api(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches",
        json={
            "name": "QA",
            "scheduled_start": when,
            "min_players": 6,
            "max_players": 10,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("M_")
    assert body["state"] == "registering"


@pytest.mark.asyncio
async def test_admin_api_rejects_games_over_twenty_players(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches",
        json={
            "name": "Too Big",
            "scheduled_start": when,
            "min_players": 3,
            "max_players": 21,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_game_via_web_form(client, reset_db):
    """The browser posts a UTC ISO string (from datetime-local JS conversion)."""
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/new",
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
async def test_web_form_rejects_games_over_twenty_players(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/new",
        data={
            "name": "Too Big",
            "scheduled_start": future,
            "min_players": "3",
            "max_players": "21",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "3 to 20" in r.text


@pytest.mark.asyncio
async def test_web_form_rejects_past_time(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/new",
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
        g = Match(
            id="G_001",
            name="t",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.commit()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches/G_001/cancel",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_export_csv_shape(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        agent, version = await make_agent(db, u, name="AI_0")
        p = Player(
            match_id="G_001",
            user_id=u.id,
            agent_id=agent.id,
            seat_name="AI_0",
            agent_version_id=version.id if version is not None else None,
            model_self_report=version.model if version is not None else None,
        )
        db.add(p)
        await db.flush()
        t = Turn(
            match_id="G_001",
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
        "/api/game-admin/hoard-hurt-help/matches/G_001/export.csv", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    text = r.text
    header = text.split("\n")[0]
    assert "match_id,round,turn,agent_id,action" in header
    assert "AI_0" in text
    assert "HOARD" in text


@pytest.mark.asyncio
async def test_export_json_includes_strategy_prompts(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        agent, version = await make_agent(db, u, name="AI_0")
        p = Player(
            match_id="G_001",
            user_id=u.id,
            agent_id=agent.id,
            seat_name="AI_0",
            agent_version_id=version.id if version is not None else None,
            model_self_report=version.model if version is not None else None,
        )
        db.add(p)
        await db.flush()
        if version is not None:
            version.strategy_text = "secret strategy"
        await db.commit()

    r = await client.get(
        "/api/game-admin/hoard-hurt-help/matches/G_001/export.json", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["players"][0]["strategy_prompt"] == "secret strategy"


# --- Role boundary tests ---


@pytest.mark.asyncio
async def test_game_admin_only_cannot_access_platform_admin(client, reset_db, monkeypatch):
    """A user who is only a game admin cannot reach the platform admin dashboard."""
    monkeypatch.setattr(settings, "platform_admin_emails", "platformonly@test.com")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "_game_admin_emails_raw", {"HOARD_HURT_HELP": "gameonly@test.com"})
    gameonly = await _seed_user(reset_db, "gameonly@test.com")
    r = await client.get("/admin", cookies=_cookies(gameonly.id), follow_redirects=False)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_platform_admin_only_cannot_access_game_admin(client, reset_db, monkeypatch):
    """A user who is only a platform admin cannot reach the game admin dashboard."""
    monkeypatch.setattr(settings, "platform_admin_emails", "platformonly@test.com")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "_game_admin_emails_raw", {"HOARD_HURT_HELP": "gameonly@test.com"})
    platformonly = await _seed_user(reset_db, "platformonly@test.com")
    r = await client.get(
        "/games/hoard-hurt-help/admin/", cookies=_cookies(platformonly.id), follow_redirects=False
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_game_admin_wrong_game_cannot_access(client, reset_db, monkeypatch):
    """A game admin for game X cannot reach the admin dashboard for game Y."""
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "_game_admin_emails_raw", {"HOARD_HURT_HELP": "gameonly@test.com"})
    gameonly = await _seed_user(reset_db, "gameonly@test.com")
    r = await client.get(
        "/games/other-game/admin/", cookies=_cookies(gameonly.id), follow_redirects=False
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_game_admin_dashboard_handles_missing_start_time(monkeypatch):
    """A bad match row should not take the whole dashboard down."""

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeDB:
        async def execute(self, _stmt):
            return FakeResult(
                [
                    SimpleNamespace(
                        id="M_9999",
                        name="Broken row",
                        scheduled_start=None,
                        current_round=0,
                        total_rounds=7,
                        state=GameState.SCHEDULED,
                    )
                ]
            )

    async def _count_players(_db, _match_id):
        return 0

    monkeypatch.setattr(game_admin_web, "_seated_player_count", _count_players)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/games/hoard-hurt-help/admin/",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )

    response = await game_admin_web.game_admin_dashboard(
        game="hoard-hurt-help",
        request=request,
        db=FakeDB(),
        user=SimpleNamespace(email="gameonly@test.com"),
    )

    assert response.context["scheduled_games"][0]["scheduled_start"] is None


@pytest.mark.asyncio
async def test_platform_admin_dashboard_handles_missing_start_time(monkeypatch):
    """The top-level admin page should also survive a broken timestamp."""

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeDB:
        async def execute(self, _stmt):
            return FakeResult(
                [
                    SimpleNamespace(
                        id="M_9999",
                        game="hoard-hurt-help",
                        name="Broken row",
                        scheduled_start=None,
                        min_players=3,
                        max_players=10,
                        state=GameState.SCHEDULED,
                    )
                ]
            )

    async def _count_players(_db, _match_id):
        return 0

    monkeypatch.setattr(admin_web, "_seated_player_count", _count_players)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )

    response = await admin_web.admin_dashboard(
        request=request,
        db=FakeDB(),
        user=SimpleNamespace(email="admin@test.com"),
    )

    assert response.context["scheduled_games"][0]["scheduled_start"] is None


@pytest.mark.asyncio
async def test_game_admin_api_accessible(client, reset_db):
    """A game admin can create a match via the game-admin API."""
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches",
        json={"name": "Boundary", "scheduled_start": when, "min_players": 6, "max_players": 10, "per_turn_deadline_seconds": 30},
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_agent_api_not_shadowed(client, reset_db):
    """The game/{match_id} agent API route is not shadowed by the game-admin router."""
    # A non-existent match returns 404 from the agent API, not a routing error.
    r = await client.get("/api/games/NOSUCHID/state")
    assert r.status_code in (401, 404, 422)  # any non-405 proves the route is reachable
