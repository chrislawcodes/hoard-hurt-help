"""Per-game strategy at entry (preset or free text); the profile library is gone."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.games import get as get_game_module
from app.main import app
from app.models import Base, Match, GameState, Player, StrategyPrompt
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


async def _seed_game_user_bot(
    reset_db: async_sessionmaker,
) -> tuple[int, int]:
    """Create a REGISTERING game + a signed-in user with one bot. Returns (user_id, bot_id)."""
    async with reset_db() as db:
        user = await make_user(db)
        await db.flush()
        bot, _ = await make_bot(db, user, name="Atlas")
        db.add(
            Match(
                id="G_001",
                name="Test Match",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                per_turn_deadline_seconds=60,
            )
        )
        await db.commit()
        return user.id, bot.id


async def _latest_strategy(reset_db: async_sessionmaker, agent_id: str) -> str:
    async with reset_db() as db:
        player = (
            await db.execute(
                select(Player).where(
                    Player.match_id == "G_001", Player.agent_id == agent_id
                )
            )
        ).scalar_one()
        prompt = (
            await db.execute(
                select(StrategyPrompt)
                .where(StrategyPrompt.player_id == player.id)
                .order_by(StrategyPrompt.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        return prompt.prompt_text


def test_pd_module_exposes_presets_and_default() -> None:
    module = get_game_module("hoard-hurt-help")
    presets = module.strategy_presets()
    assert len(presets) >= 1
    for p in presets:
        assert p.id and p.name and p.prompt
    assert module.default_strategy().strip()


@pytest.mark.asyncio
async def test_join_with_custom_strategy_seeds_it(client, reset_db) -> None:
    user_id, bot_id = await _seed_game_user_bot(reset_db)
    r = await client.post(
        "/games/G_001/join",
        data={
            "bot_id": bot_id,
            "display_name": "AI_qa",
            "strategy_prompt": "CUSTOM: always cooperate.",
        },
        cookies=_signed_in_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert await _latest_strategy(reset_db, "AI_qa") == "CUSTOM: always cooperate."


@pytest.mark.asyncio
async def test_join_without_strategy_uses_module_default(client, reset_db) -> None:
    user_id, bot_id = await _seed_game_user_bot(reset_db)
    r = await client.post(
        "/games/G_001/join",
        data={"bot_id": bot_id, "display_name": "AI_def"},
        cookies=_signed_in_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    seeded = await _latest_strategy(reset_db, "AI_def")
    assert seeded == get_game_module("hoard-hurt-help").default_strategy()


@pytest.mark.asyncio
async def test_join_form_offers_presets(client, reset_db) -> None:
    user_id, bot_id = await _seed_game_user_bot(reset_db)
    r = await client.get("/games/G_001/join", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    # The preset picker + write-your-own textarea are present.
    assert 'id="preset-picker"' in r.text
    assert 'name="strategy_prompt"' in r.text
    assert "Tit-for-Tat" in r.text


@pytest.mark.asyncio
async def test_strategy_profiles_route_removed(client, reset_db) -> None:
    user_id, _ = await _seed_game_user_bot(reset_db)
    r = await client.get(
        "/me/strategy-profiles", cookies=_signed_in_cookies(user_id)
    )
    assert r.status_code == 404
