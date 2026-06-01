"""FastAPI dependencies shared across routes."""

import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth.session import get_user_from_session
from app.db import get_session
from app.engine.bot_activity import mark_seen
from app.engine.tokens import bot_key_lookup
from app.models.bot import Bot, BotStatus
from app.models.player import Player
from app.models.user import User

logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, db: DbSession) -> User | None:
    """Return the signed-in User or None (does not raise)."""
    return await get_user_from_session(request, db)


async def require_user(request: Request, db: DbSession) -> User:
    user = await get_user_from_session(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "NOT_SIGNED_IN",
                    "message": "Sign in with Google to continue.",
                    "details": {},
                }
            },
        )
    return user


async def require_admin(request: Request, db: DbSession) -> User:
    user = await require_user(request, db)
    if user.email.lower() not in settings.admin_emails_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "NOT_ADMIN",
                    "message": "Admin access required.",
                    "details": {},
                }
            },
        )
    return user


async def require_bot(
    db: DbSession,
    x_agent_key: Annotated[str | None, Header()] = None,
) -> Bot:
    """Validate `X-Agent-Key` as a stable bot key and return the Bot.

    Lookup is O(1) via the indexed sha256 `key_lookup`. A paused bot is rejected
    here so every agent route fails fast with BOT_PAUSED.
    """
    if not x_agent_key:
        logger.warning("agent auth failed: missing X-Agent-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_KEY",
                    "message": "Missing X-Agent-Key header.",
                    "details": {},
                }
            },
        )
    # Archived (deleted) bots are excluded: their key no longer authenticates,
    # so the bot stops playing and is treated exactly like an unknown key.
    # A graceful reissue keeps the previous key valid too (prev_key_lookup), so
    # match either — the old key works until the new one is first used, at which
    # point mark_seen clears it.
    key_hash = bot_key_lookup(x_agent_key)
    bot = (
        await db.execute(
            select(Bot).where(
                or_(Bot.key_lookup == key_hash, Bot.prev_key_lookup == key_hash),
                Bot.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if bot is None:
        # Log enough to diagnose without ever recording the secret itself.
        logger.warning("agent auth failed: no bot for key prefix %s", x_agent_key[:11])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_KEY",
                    "message": "Invalid X-Agent-Key.",
                    "details": {},
                }
            },
        )
    if bot.status == BotStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "BOT_PAUSED",
                    "message": "This bot is paused; resume it to play.",
                    "details": {},
                }
            },
        )
    # Records first-connect (announced once), refreshes the heartbeat, and retires
    # a superseded key once the new one is used. This is the single choke point
    # all agent paths cross, so it covers the runner, MCP, and the direct API.
    await mark_seen(db, bot, key_hash=key_hash)
    return bot


async def require_bot_player(
    game_id: Annotated[str, Path()],
    bot: Annotated[Bot, Depends(require_bot)],
    db: DbSession,
) -> Player:
    """Resolve the authenticated bot's active player in {game_id}.

    One player per (bot, game), so this is unambiguous. 404 if the bot has no
    player in that game.
    """
    player = (
        await db.execute(
            select(Player).where(
                Player.bot_id == bot.id,
                Player.game_id == game_id,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_IN_GAME",
                    "message": "This bot has no player in that game.",
                    "details": {},
                }
            },
        )
    return player
