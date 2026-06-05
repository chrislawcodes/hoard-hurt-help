"""Bot-specific support for the self-serve web routes."""

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import DbSession
from app.engine.sim_presets import sim_presets
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotKind
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User

_BOT_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]{1,120}$")
_BOT_NAME_ERROR = "Bot name must be 1–120 letters, numbers, spaces, _ or -."


async def get_owned_bot(db: DbSession, user: User, bot_id: int) -> Bot:
    bot = (
        await db.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id))
    ).scalar_one_or_none()
    if bot is None:
        raise HTTPException(404, detail="Bot not found.")
    return bot


def validate_bot_name(name: str) -> str:
    name = name.strip()
    if not _BOT_NAME_RE.fullmatch(name):
        raise HTTPException(400, detail=_BOT_NAME_ERROR)
    return name


def archived_bot_name(base: str, archived_at: datetime, extra: str = "") -> str:
    """Return a renamed-on-archive label that still fits the name column."""
    suffix = f" (archived {archived_at:%Y-%m-%d %H:%M}{extra})"
    return f"{base[: 120 - len(suffix)]}{suffix}"


async def bot_game_rows(db: DbSession, bot: Bot) -> list[dict[str, Any]]:
    """Each game the bot is currently in, with state and current score."""
    players = (
        (
            await db.execute(
                select(Player).where(Player.bot_id == bot.id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for p in players:
        g = (await db.execute(select(Match).where(Match.id == p.match_id))).scalar_one()
        out.append(
            {
                "match_id": g.id,
                "game_type": g.game,
                "name": g.name,
                "state": g.state.value,
                "agent_id": p.agent_id,
                "round_score": p.current_round_score,
                "total_score": p.total_round_score,
                "player_id": p.id,
                "pre_game": g.state in (GameState.SCHEDULED, GameState.REGISTERING),
            }
        )
    return out


async def ensure_preset_sim_bots(db: DbSession, user: User) -> None:
    presets = sim_presets()
    existing = (
        (
            await db.execute(
                select(Bot).where(
                    Bot.user_id == user.id,
                    Bot.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_profile = {
        bot.sim_profile_id: bot
        for bot in existing
        if bot.kind == BotKind.SIM and bot.sim_profile_id
    }
    created = False
    for preset in presets:
        if preset.id in by_profile:
            continue
        name = preset.name
        key = generate_bot_key()
        bot = Bot(
            user_id=user.id,
            name=name,
            key_lookup=bot_key_lookup(key),
            key_hint=bot_key_hint(key),
            kind=BotKind.SIM,
            sim_profile_id=preset.id,
            sim_profile_name=preset.name,
            sim_strategy=preset.strategy,
            sim_truthfulness=preset.truthfulness,
            sim_trust_model=preset.trust_model,
            sim_version="v1",
        )
        db.add(bot)
        await db.flush()
        bot.sim_seed = bot.id + preset.seed_offset
        created = True
    if created:
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
