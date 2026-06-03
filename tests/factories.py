"""Shared test factories for the bot-based model.

A player now belongs to a bot, and auth is by the bot's stable key. These keep
the per-test seed code short and consistent. `seat_player` stashes the bot's
plaintext key on `player._test_key` for use as the X-Agent-Key header.
"""

from __future__ import annotations

from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotKind
from app.models.player import Player
from app.models.user import User


async def make_user(db, i: int = 0) -> User:
    u = User(google_sub=f"sub-{i}", email=f"u{i}@t.com")
    db.add(u)
    await db.flush()
    return u


async def make_bot(
    db,
    user: User,
    name: str | None = None,
    key: str | None = None,
    *,
    kind: BotKind = BotKind.EXTERNAL,
    sim_strategy: str | None = None,
    sim_truthfulness: int | None = None,
    sim_trust_model: str | None = None,
    sim_seed: int | None = None,
    sim_version: str | None = None,
    sim_fixture_pack: str | None = None,
) -> tuple[Bot, str]:
    key = key or generate_bot_key()
    bot = Bot(
        user_id=user.id,
        name=name or f"bot-{user.id}",
        key_lookup=bot_key_lookup(key),
        key_hint=bot_key_hint(key),
        kind=kind,
        sim_strategy=sim_strategy,
        sim_truthfulness=sim_truthfulness,
        sim_trust_model=sim_trust_model,
        sim_seed=sim_seed,
        sim_version=sim_version,
        sim_fixture_pack=sim_fixture_pack,
    )
    db.add(bot)
    await db.flush()
    return bot, key


async def seat_player(
    db,
    game_id: str,
    agent_id: str,
    i: int = 0,
    user: User | None = None,
    key: str | None = None,
) -> Player:
    """Create user + bot + player for a game (one bot per player, distinct keys).

    The bot's plaintext key is stashed on `player._test_key`.
    """
    if user is None:
        user = await make_user(db, i)
    bot, key = await make_bot(db, user, name=f"bot-{agent_id}", key=key)
    p = Player(game_id=game_id, user_id=user.id, bot_id=bot.id, agent_id=agent_id)
    p._test_key = key  # type: ignore[attr-defined]
    db.add(p)
    await db.flush()
    return p
