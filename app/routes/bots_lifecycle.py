"""Bot rename, pause/resume, and delete actions."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Form, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.deps import DbSession, require_user
from app.models.bot import Bot, BotKind, BotStatus
from app.models.player import Player
from app.models.user import User
from app.routes.bots_web_support import (
    archived_bot_name,
    get_owned_bot,
    validate_bot_name,
)

router = APIRouter()


@router.post("/{bot_id}/rename")
async def rename_bot(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
) -> RedirectResponse:
    """Rename a bot. The connection code is unaffected — only the label changes."""
    bot = await get_owned_bot(db, user, bot_id)
    name = validate_bot_name(name)
    clash = (
        await db.execute(
            select(Bot).where(
                Bot.user_id == user.id, Bot.name == name, Bot.id != bot.id
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(409, detail="You already have a bot with that name.")
    bot.name = name
    await db.commit()
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{bot_id}/pause")
async def pause_bot(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    bot = await get_owned_bot(db, user, bot_id)
    bot.status = BotStatus.PAUSED
    bot.paused_at = datetime.now(timezone.utc)
    bot.paused_reason = "owner"
    await db.commit()
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{bot_id}/resume")
async def resume_bot(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    bot = await get_owned_bot(db, user, bot_id)
    bot.status = BotStatus.ACTIVE
    bot.paused_at = None
    bot.paused_reason = None
    await db.commit()
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{bot_id}/delete")
async def delete_bot(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    bot = await get_owned_bot(db, user, bot_id)
    # Player rows reference bots, so game history must be preserved by archiving.
    has_players = (
        await db.execute(select(Player.id).where(Player.bot_id == bot.id).limit(1))
    ).first()
    if has_players is not None:
        now = datetime.now(timezone.utc)
        bot.archived_at = now
        bot.status = BotStatus.PAUSED
        bot.paused_at = now
        bot.paused_reason = "deleted"
        if bot.kind == BotKind.SIM:
            bot.sim_profile_id = None
            bot.sim_profile_name = None
        stamped = archived_bot_name(bot.name, now)
        clash = (
            await db.execute(
                select(Bot.id)
                .where(
                    Bot.user_id == bot.user_id,
                    Bot.name == stamped,
                    Bot.id != bot.id,
                )
                .limit(1)
            )
        ).first()
        bot.name = archived_bot_name(bot.name, now, f" #{bot.id}") if clash else stamped
    else:
        await db.delete(bot)
    await db.commit()
    return RedirectResponse(url="/me/bots", status_code=status.HTTP_303_SEE_OTHER)
