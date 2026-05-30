"""Lobby + join + dashboard tests."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.tokens import hash_agent_key, verify_agent_key
from app.main import app
from app.models import Base, Game, GameState, Player, User


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
    # Feature 002: the setup prompt teaches the new summary, messaging, and pulls.
    assert "summary" in r.text
    assert "messages aimed at you" in r.text
    assert "get_opponent_history" in r.text


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
    assert r.headers["location"].startswith("/me/players/")


@pytest.mark.asyncio
async def test_user_can_join_multiple_bots_in_one_game(client, reset_db):
    """A single user may register several bots (distinct names) in one game."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    cookies = _signed_in_cookies(client, user.id)

    locations = []
    for name in ("AI_one", "AI_two"):
        r = await client.post(
            "/games/G_001/join",
            data={"display_name": name, "strategy_prompt": "x"},
            cookies=cookies,
            follow_redirects=False,
        )
        assert r.status_code == 303
        locations.append(r.headers["location"])

    # Two distinct player dashboards — not bounced back to a single slot.
    assert all(loc.startswith("/me/players/") for loc in locations)
    assert locations[0] != locations[1]

    async with reset_db() as db:
        players = (
            (
                await db.execute(
                    select(Player).where(
                        Player.game_id == "G_001", Player.user_id == user.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {p.agent_id for p in players} == {"AI_one", "AI_two"}


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


async def _seed_player(
    reset_db: async_sessionmaker,
    user: User,
    game: Game,
    key: str,
    agent_id: str = "AI_qa",
) -> int:
    """Create a player owned by `user` in `game`, keyed with `key`. Returns its id."""
    async with reset_db() as db:
        p = Player(
            game_id=game.id,
            user_id=user.id,
            agent_id=agent_id,
            agent_key_hash=hash_agent_key(key),
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p.id


async def _key_hash(reset_db: async_sessionmaker, player_id: int) -> str:
    async with reset_db() as db:
        p = (
            await db.execute(select(Player).where(Player.id == player_id))
        ).scalar_one()
        return p.agent_key_hash


@pytest.mark.asyncio
async def test_dashboard_visit_does_not_rotate_key(client, reset_db):
    """Loading the dashboard pre-game must NOT change the agent key.

    Regression: a previous version regenerated the key on every pre-game visit,
    silently invalidating any bot already configured with it.
    """
    user = await _seed_user(reset_db)
    game = await _seed_game(reset_db)
    key = "sk_game_" + "a" * 48
    player_id = await _seed_player(reset_db, user, game, key)

    # Visit the dashboard twice.
    for _ in range(2):
        r = await client.get(
            f"/me/players/{player_id}", cookies=_signed_in_cookies(client, user.id)
        )
        assert r.status_code == 200

    # The original key still verifies — it was never rotated out from under us.
    assert verify_agent_key(key, await _key_hash(reset_db, player_id))


@pytest.mark.asyncio
async def test_rekey_invalidates_old_key(client, reset_db):
    """Re-issue is the one deliberate path that changes the key."""
    user = await _seed_user(reset_db)
    game = await _seed_game(reset_db)
    key = "sk_game_" + "b" * 48
    player_id = await _seed_player(reset_db, user, game, key)

    r = await client.post(
        f"/me/players/{player_id}/rekey",
        cookies=_signed_in_cookies(client, user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/me/players/{player_id}"

    # The old key no longer works; the hash changed to a freshly issued key.
    assert not verify_agent_key(key, await _key_hash(reset_db, player_id))


@pytest.mark.asyncio
async def test_rekey_blocked_after_game_starts(client, reset_db):
    """A key can't be rotated mid-game — that would strand a playing bot."""
    user = await _seed_user(reset_db)
    game = await _seed_game(reset_db, state=GameState.ACTIVE)
    key = "sk_game_" + "c" * 48
    player_id = await _seed_player(reset_db, user, game, key)

    r = await client.post(
        f"/me/players/{player_id}/rekey",
        cookies=_signed_in_cookies(client, user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    # Key is untouched.
    assert verify_agent_key(key, await _key_hash(reset_db, player_id))


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
