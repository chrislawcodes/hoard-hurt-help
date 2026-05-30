"""Strategy-profile CRUD + seed-at-entry (copy) tests."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.main import app
from app.models import (
    Base,
    Game,
    GameState,
    Player,
    StrategyProfile,
    StrategyPrompt,
    User,
)
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


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(
        json.dumps({"user_id": user_id, "next_after_login": None}).encode()
    ).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db)
        await db.commit()
        await db.refresh(u)
        return u


async def _profiles(reset_db: async_sessionmaker, user_id: int):
    async with reset_db() as db:
        return (
            (
                await db.execute(
                    select(StrategyProfile)
                    .where(StrategyProfile.user_id == user_id)
                    .order_by(StrategyProfile.name)
                )
            )
            .scalars()
            .all()
        )


async def _seed_game_and_bot(reset_db: async_sessionmaker, user: User) -> tuple[str, int]:
    async with reset_db() as db:
        g = Game(
            id="G_001",
            name="T",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        bot, _ = await make_bot(db, u, name="Atlas")
        await db.commit()
        return g.id, bot.id


async def _player_strategy(reset_db: async_sessionmaker, game_id: str) -> str:
    async with reset_db() as db:
        p = (
            await db.execute(select(Player).where(Player.game_id == game_id))
        ).scalar_one()
        sp = (
            (
                await db.execute(
                    select(StrategyPrompt)
                    .where(StrategyPrompt.player_id == p.id)
                    .order_by(StrategyPrompt.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert sp is not None
        return sp.prompt_text


@pytest.mark.asyncio
async def test_create_first_profile_is_default(client, reset_db):
    user = await _seed_user(reset_db)
    r = await client.post(
        "/me/strategy-profiles",
        data={"name": "TitForTat", "prompt_text": "cooperate then mirror"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    profiles = await _profiles(reset_db, user.id)
    assert len(profiles) == 1
    assert profiles[0].is_default is True  # first profile auto-defaults


@pytest.mark.asyncio
async def test_single_default_invariant(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _cookies(user.id)
    await client.post(
        "/me/strategy-profiles",
        data={"name": "A", "prompt_text": "a"},
        cookies=cookies,
        follow_redirects=False,
    )
    await client.post(
        "/me/strategy-profiles",
        data={"name": "B", "prompt_text": "b", "is_default": "on"},
        cookies=cookies,
        follow_redirects=False,
    )
    profiles = await _profiles(reset_db, user.id)
    defaults = [p for p in profiles if p.is_default]
    assert len(defaults) == 1
    assert defaults[0].name == "B"


@pytest.mark.asyncio
async def test_update_and_delete(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _cookies(user.id)
    await client.post(
        "/me/strategy-profiles",
        data={"name": "A", "prompt_text": "a"},
        cookies=cookies,
        follow_redirects=False,
    )
    [p] = await _profiles(reset_db, user.id)
    r = await client.post(
        f"/me/strategy-profiles/{p.id}",
        data={"name": "A2", "prompt_text": "a2"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303
    [p2] = await _profiles(reset_db, user.id)
    assert p2.name == "A2"
    assert p2.prompt_text == "a2"
    r = await client.post(
        f"/me/strategy-profiles/{p.id}/delete",
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert await _profiles(reset_db, user.id) == []


@pytest.mark.asyncio
async def test_duplicate_name_rejected(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _cookies(user.id)
    await client.post(
        "/me/strategy-profiles",
        data={"name": "Dup", "prompt_text": "x"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/me/strategy-profiles",
        data={"name": "Dup", "prompt_text": "y"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_entry_seeds_from_chosen_profile(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _cookies(user.id)
    await client.post(
        "/me/strategy-profiles",
        data={"name": "Chosen", "prompt_text": "MY CHOSEN STRATEGY"},
        cookies=cookies,
        follow_redirects=False,
    )
    [profile] = await _profiles(reset_db, user.id)
    game_id, bot_id = await _seed_game_and_bot(reset_db, user)
    r = await client.post(
        f"/games/{game_id}/join",
        data={
            "bot_id": bot_id,
            "display_name": "AI_qa",
            "strategy_profile_id": str(profile.id),
        },
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert await _player_strategy(reset_db, game_id) == "MY CHOSEN STRATEGY"


@pytest.mark.asyncio
async def test_entry_uses_default_when_none_chosen(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _cookies(user.id)
    await client.post(
        "/me/strategy-profiles",
        data={"name": "Def", "prompt_text": "DEFAULT STRATEGY"},
        cookies=cookies,
        follow_redirects=False,
    )  # auto-default
    game_id, bot_id = await _seed_game_and_bot(reset_db, user)
    r = await client.post(
        f"/games/{game_id}/join",
        data={"bot_id": bot_id, "display_name": "AI_qa", "strategy_profile_id": ""},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert await _player_strategy(reset_db, game_id) == "DEFAULT STRATEGY"


@pytest.mark.asyncio
async def test_profile_edit_does_not_change_running_game(client, reset_db):
    """Copy-at-entry: editing a profile after entry must not change the player."""
    user = await _seed_user(reset_db)
    cookies = _cookies(user.id)
    await client.post(
        "/me/strategy-profiles",
        data={"name": "Live", "prompt_text": "ORIGINAL"},
        cookies=cookies,
        follow_redirects=False,
    )
    [profile] = await _profiles(reset_db, user.id)
    game_id, bot_id = await _seed_game_and_bot(reset_db, user)
    await client.post(
        f"/games/{game_id}/join",
        data={
            "bot_id": bot_id,
            "display_name": "AI_qa",
            "strategy_profile_id": str(profile.id),
        },
        cookies=cookies,
        follow_redirects=False,
    )
    await client.post(
        f"/me/strategy-profiles/{profile.id}",
        data={"name": "Live", "prompt_text": "CHANGED"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert await _player_strategy(reset_db, game_id) == "ORIGINAL"
