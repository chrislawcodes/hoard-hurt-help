"""Bot soft-delete: one Delete action that does the right thing.

A bot with no game history is hard-deleted (its row is gone). A bot that has
ever been in a game is soft-deleted instead — archived and paused, its row kept
so the players that FK back to it keep their history. An archived bot is hidden
from the owner's lists, rejected from new games, and its key stops working.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.sim_presets import sim_presets
from app.main import app
from app.models import Base, Bot, BotKind, BotStatus, Match, GameState, Player, User
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


async def _seed_game(
    reset_db: async_sessionmaker, state=GameState.REGISTERING, match_id: str = "G_001"
) -> Match:
    async with reset_db() as db:
        g = Match(
            id=match_id,
            name="Test Match",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


async def _seed_bot(
    reset_db: async_sessionmaker, user: User, name: str = "Atlas"
) -> tuple[int, str]:
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        bot, key = await make_bot(db, u, name=name)
        await db.commit()
        return bot.id, key


async def _give_history(reset_db: async_sessionmaker, user: User, bot_id: int) -> None:
    """Seat the bot as a player in a game so it has game history."""
    await _seed_game(reset_db)
    async with reset_db() as db:
        db.add(Player(match_id="G_001", user_id=user.id, bot_id=bot_id, agent_id="atlas"))
        await db.commit()


async def _get_bot(reset_db: async_sessionmaker, bot_id: int) -> Bot | None:
    async with reset_db() as db:
        return (
            await db.execute(select(Bot).where(Bot.id == bot_id))
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_delete_without_history_hard_deletes(client, reset_db):
    """A bot that never played is removed entirely."""
    user = await _seed_user(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user)

    r = await client.post(
        f"/me/bots/{bot_id}/delete",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/me/bots"
    assert await _get_bot(reset_db, bot_id) is None


@pytest.mark.asyncio
async def test_delete_with_history_archives_instead(client, reset_db):
    """A bot with game history is archived + paused, not removed."""
    user = await _seed_user(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user)
    await _give_history(reset_db, user, bot_id)

    r = await client.post(
        f"/me/bots/{bot_id}/delete",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    bot = await _get_bot(reset_db, bot_id)
    assert bot is not None, "bot with history must be kept, not deleted"
    assert bot.archived_at is not None
    assert bot.status == BotStatus.PAUSED
    assert bot.paused_reason == "deleted"


@pytest.mark.asyncio
async def test_archived_bot_hidden_from_my_bots(client, reset_db):
    """An archived bot no longer appears in the owner's bot list."""
    user = await _seed_user(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user, name="Ghostbot")
    await _give_history(reset_db, user, bot_id)
    await client.post(
        f"/me/bots/{bot_id}/delete", cookies=_signed_in_cookies(user.id)
    )

    r = await client.get("/me/bots", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Ghostbot" not in r.text


@pytest.mark.asyncio
async def test_archived_bot_key_stops_authenticating(client, reset_db):
    """Once archived, the bot's key is rejected like an unknown key."""
    user = await _seed_user(reset_db)
    bot_id, key = await _seed_bot(reset_db, user)
    await _give_history(reset_db, user, bot_id)  # seats the bot in G_001

    # Sanity: the key works before deletion.
    ok = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key})
    assert ok.status_code != 401

    await client.post(
        f"/me/bots/{bot_id}/delete", cookies=_signed_in_cookies(user.id)
    )

    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key})
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "INVALID_KEY"


@pytest.mark.asyncio
async def test_archived_bot_cannot_join_new_game(client, reset_db):
    """A crafted join POST naming an archived bot is rejected."""
    user = await _seed_user(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user)
    await _give_history(reset_db, user, bot_id)
    await client.post(
        f"/me/bots/{bot_id}/delete", cookies=_signed_in_cookies(user.id)
    )
    await _seed_game(reset_db, state=GameState.REGISTERING, match_id="G_002")

    r = await client.post(
        "/games/hoard-hurt-help/matches/G_002/join",
        data={"bot_id": bot_id, "display_name": "atlas2", "strategy_prompt": ""},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_archiving_frees_name_for_reuse(client, reset_db):
    """After a bot is archived, its original name can be used by a new bot."""
    user = await _seed_user(reset_db)
    bot_id, _ = await _seed_bot(reset_db, user, name="Atlas")
    await _give_history(reset_db, user, bot_id)
    await client.post(
        f"/me/bots/{bot_id}/delete", cookies=_signed_in_cookies(user.id)
    )

    # The archived copy is renamed so the live name "Atlas" is free again.
    archived = await _get_bot(reset_db, bot_id)
    assert archived is not None
    assert archived.name.startswith("Atlas (archived ")
    assert len(archived.name) <= 120

    # Creating a fresh "Atlas" now succeeds instead of 409-ing on the name.
    r = await client.post(
        "/me/bots",
        data={"name": "Atlas"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303


@pytest.mark.asyncio
async def test_archived_name_fits_120_char_column(client, reset_db):
    """A max-length (120-char) name is truncated so the stamped copy still fits."""
    user = await _seed_user(reset_db)
    long_name = "B" * 120
    bot_id, _ = await _seed_bot(reset_db, user, name=long_name)
    await _give_history(reset_db, user, bot_id)
    await client.post(
        f"/me/bots/{bot_id}/delete", cookies=_signed_in_cookies(user.id)
    )

    archived = await _get_bot(reset_db, bot_id)
    assert archived is not None
    assert len(archived.name) <= 120
    assert archived.name.endswith(")")
    assert "(archived " in archived.name


@pytest.mark.asyncio
async def test_deleted_preset_sim_can_be_reprovisioned(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)

    await client.get("/me/bots", cookies=cookies)
    presets = sim_presets()
    async with reset_db() as db:
        sim_bot = (
            await db.execute(
                select(Bot).where(
                    Bot.user_id == user.id,
                    Bot.kind == BotKind.SIM,
                    Bot.archived_at.is_(None),
                )
            )
        ).scalars().first()
        assert sim_bot is not None
        db.add(
            Match(
                id="G_SIM",
                name="Sim Match",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.add(
            Player(
                match_id="G_SIM",
                user_id=user.id,
                bot_id=sim_bot.id,
                agent_id="AI_SIM",
            )
        )
        await db.commit()
        profile_id = sim_bot.sim_profile_id

    r = await client.post(
        f"/me/bots/{sim_bot.id}/delete",
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        archived = (await db.execute(select(Bot).where(Bot.id == sim_bot.id))).scalar_one()
    assert archived.sim_profile_id is None

    r2 = await client.get("/me/bots", cookies=cookies)
    assert r2.status_code == 200

    async with reset_db() as db:
        sim_bots = (
            await db.execute(
                select(Bot).where(
                    Bot.user_id == user.id,
                    Bot.kind == BotKind.SIM,
                    Bot.archived_at.is_(None),
                )
            )
        ).scalars().all()
    assert len(sim_bots) == len(presets)
    assert any(bot.sim_profile_id == profile_id for bot in sim_bots if bot.id != sim_bot.id)
