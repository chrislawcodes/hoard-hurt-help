"""Lobby, bot management, and game-entry web tests (bot model)."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.tokens import bot_key_lookup
from app.main import app
from app.models import Base, Bot, Game, GameState, Player, User
from tests.factories import make_bot, make_user


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


def _signed_in_cookies(user_id: int) -> dict:
    """A Starlette session cookie marking this user as signed-in (prod secret)."""
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db)
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


async def _seed_bot(
    reset_db: async_sessionmaker, user: User, key: str | None = None, name: str = "Atlas"
) -> tuple[int, str]:
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        bot, k = await make_bot(db, u, name=name, key=key)
        await db.commit()
        return bot.id, k


@pytest.mark.asyncio
async def test_home_renders(client, reset_db):
    await _seed_game(reset_db)
    r = await client.get("/")
    assert r.status_code == 200
    assert "Test Game" in r.text


@pytest.mark.asyncio
async def test_join_requires_sign_in(client, reset_db):
    await _seed_game(reset_db)
    r = await client.get("/games/G_001/join", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/google/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_create_bot_shows_key_once(client, reset_db):
    user = await _seed_user(reset_db)
    r = await client.post(
        "/me/bots",
        data={"name": "Atlas"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "sk_bot_" in r.text  # one-time code + paste-once snippet shown
    assert "get_next_turn" in r.text

    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.user_id == user.id))).scalar_one()
    r2 = await client.get(f"/me/bots/{bot.id}", cookies=_signed_in_cookies(user.id))
    assert r2.status_code == 200
    assert "sk_bot_" not in r2.text  # never shown again
    assert "Reissue" in r2.text


@pytest.mark.asyncio
async def test_bot_detail_does_not_rotate_key(client, reset_db):
    """Regression: visiting the bot page must not change the key."""
    user = await _seed_user(reset_db)
    key = "sk_bot_" + "a" * 48
    bot_id, _ = await _seed_bot(reset_db, user, key=key)
    for _ in range(2):
        r = await client.get(f"/me/bots/{bot_id}", cookies=_signed_in_cookies(user.id))
        assert r.status_code == 200
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
    assert bot.key_lookup == bot_key_lookup(key)


@pytest.mark.asyncio
async def test_reissue_invalidates_old_key_anytime(client, reset_db):
    """Reissue is the deliberate path that changes the key — allowed any time."""
    user = await _seed_user(reset_db)
    game = await _seed_game(reset_db, state=GameState.ACTIVE)  # even mid-game
    key = "sk_bot_" + "b" * 48
    bot_id, _ = await _seed_bot(reset_db, user, key=key)
    # Bot is in the active game.
    async with reset_db() as db:
        db.add(Player(game_id=game.id, user_id=user.id, bot_id=bot_id, agent_id="AI_x"))
        await db.commit()

    r = await client.post(
        f"/me/bots/{bot_id}/reissue",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
    assert bot.key_lookup != bot_key_lookup(key)  # old key no longer resolves


@pytest.mark.asyncio
async def test_enter_bot_into_game(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user)
    r = await client.post(
        "/games/G_001/join",
        data={"bot_id": bot_id, "display_name": "AI_qa"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/me/bots/{bot_id}"
    async with reset_db() as db:
        p = (
            await db.execute(select(Player).where(Player.game_id == "G_001"))
        ).scalar_one()
    assert p.bot_id == bot_id
    assert p.agent_id == "AI_qa"


@pytest.mark.asyncio
async def test_duplicate_bot_entry_blocked(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user)
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/G_001/join",
        data={"bot_id": bot_id, "display_name": "AI_a"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/games/G_001/join",
        data={"bot_id": bot_id, "display_name": "AI_b"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already in this game" in r.text


@pytest.mark.asyncio
async def test_two_bots_one_game(client, reset_db):
    """A user fields multiple agents by running multiple bots."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    b1, _ = await _seed_bot(reset_db, user, name="One")
    b2, _ = await _seed_bot(reset_db, user, name="Two")
    cookies = _signed_in_cookies(user.id)
    for bid, name in [(b1, "AI_one"), (b2, "AI_two")]:
        r = await client.post(
            "/games/G_001/join",
            data={"bot_id": bid, "display_name": name},
            cookies=cookies,
            follow_redirects=False,
        )
        assert r.status_code == 303
    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.game_id == "G_001")))
            .scalars()
            .all()
        )
    assert {p.agent_id for p in players} == {"AI_one", "AI_two"}


@pytest.mark.asyncio
async def test_name_taken_blocked(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    b1, _ = await _seed_bot(reset_db, user, name="One")
    b2, _ = await _seed_bot(reset_db, user, name="Two")
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/G_001/join",
        data={"bot_id": b1, "display_name": "Dup"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/games/G_001/join",
        data={"bot_id": b2, "display_name": "Dup"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "already taken" in r.text


@pytest.mark.asyncio
async def test_rename_bot(client, reset_db):
    user = await _seed_user(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user, name="OldName")
    r = await client.post(
        f"/me/bots/{bot_id}/rename",
        data={"name": "NewName"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        bot = (await db.execute(select(Bot).where(Bot.id == bot_id))).scalar_one()
    assert bot.name == "NewName"


@pytest.mark.asyncio
async def test_rename_duplicate_blocked(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_bot(reset_db, user, name="Taken")
    bot_id, _ = await _seed_bot(reset_db, user, name="Mine")
    r = await client.post(
        f"/me/bots/{bot_id}/rename",
        data={"name": "Taken"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_my_games_lists_user_games(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user)
    await client.post(
        "/games/G_001/join",
        data={"bot_id": bot_id, "display_name": "AI_qa"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    r = await client.get("/me/games", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Test Game" in r.text
