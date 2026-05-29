"""Lobby + join + dashboard tests."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, Game, GameState, User


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

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = User(google_sub="qa-sub", email="qa@test.com", name="QA")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_game(reset_db: async_sessionmaker, state=GameState.REGISTERING) -> Game:
    async with reset_db() as db:
        g = Game(
            id="G_001",
            name="Test Game",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


@pytest.mark.asyncio
async def test_home_renders(client, reset_db):
    await _seed_game(reset_db)
    r = await client.get("/")
    assert r.status_code == 200
    assert "Test Game" in r.text
    assert "Sign in with Google" in r.text


@pytest.mark.asyncio
async def test_join_form_requires_sign_in(client, reset_db):
    await _seed_game(reset_db)
    r = await client.get("/games/G_001/join", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/google/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_join_form_shows_ai_setup(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    r = await client.get(
        "/games/G_001/join",
        cookies=_signed_in_cookies(client, user.id),
    )
    assert r.status_code == 200
    # AI picker and setup instructions (with embedded key) should be present.
    assert "Which AI are you using?" in r.text
    assert "claude mcp add hoardhurthelp" in r.text
    assert "X-Agent-Key" in r.text


@pytest.mark.asyncio
async def test_join_creates_player_and_redirects(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    r = await client.post(
        "/games/G_001/join",
        data={"display_name": "AI_qa", "strategy_prompt": "be cool"},
        cookies=_signed_in_cookies(client, user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/me/games/G_001"


@pytest.mark.asyncio
async def test_my_games_lists_user_games(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    # Join.
    await client.post(
        "/games/G_001/join",
        data={"display_name": "AI_qa", "strategy_prompt": "x"},
        cookies=_signed_in_cookies(client, user.id),
        follow_redirects=False,
    )
    r = await client.get(
        "/me/games", cookies=_signed_in_cookies(client, user.id)
    )
    assert r.status_code == 200
    assert "Test Game" in r.text


def _signed_in_cookies(client: AsyncClient, user_id: int) -> dict:
    """Construct a Starlette session cookie marking this user as signed-in.

    Starlette's SessionMiddleware uses itsdangerous over a base64-encoded JSON
    payload; we use the production secret. The auth flow is exercised end-to-end
    in test_auth.py — here we shortcut for UX tests.
    """
    import base64
    import json
    from itsdangerous import TimestampSigner

    from app.config import settings

    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    signed = signer.sign(payload).decode()
    return {"hhh_session": signed}
