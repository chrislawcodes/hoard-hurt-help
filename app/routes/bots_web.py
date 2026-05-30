"""Web routes for the self-serve "My Bots" control panel.

Create a bot (one-time credential + paste-once MCP snippet), see the games it is
in and their scores, reissue the credential (any time), pause/resume (the kill
switch), and delete. All under the signed-in user.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession, require_user
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotStatus
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.user import User
from app.templating import templates

router = APIRouter(prefix="/me/bots", tags=["bots"])
logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]{1,64}$")


def _is_admin(user: User) -> bool:
    return user.email.lower() in settings.admin_emails_set


async def _owned_bot(db: DbSession, user: User, bot_id: int) -> Bot:
    bot = (
        await db.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id))
    ).scalar_one_or_none()
    if bot is None:
        raise HTTPException(404, detail="Bot not found.")
    return bot


async def _bot_games(db: DbSession, bot: Bot) -> list[dict[str, Any]]:
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
        g = (await db.execute(select(Game).where(Game.id == p.game_id))).scalar_one()
        out.append(
            {
                "game_id": g.id,
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


@router.get("", response_class=HTMLResponse)
async def list_bots(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    bots = (
        (await db.execute(select(Bot).where(Bot.user_id == user.id).order_by(Bot.name)))
        .scalars()
        .all()
    )
    rows = [{"bot": b, "games": await _bot_games(db, b)} for b in bots]
    return templates.TemplateResponse(
        request,
        "bots/list.html",
        {"user": user, "is_admin": _is_admin(user), "rows": rows},
    )


@router.post("")
async def create_bot(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
):
    name = name.strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(
            400, detail="Bot name must be 1–64 letters, numbers, spaces, _ or -."
        )
    existing = (
        await db.execute(select(Bot).where(Bot.user_id == user.id, Bot.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, detail="You already have a bot with that name.")

    key = generate_bot_key()
    bot = Bot(
        user_id=user.id,
        name=name,
        key_lookup=bot_key_lookup(key),
        key_hint=bot_key_hint(key),
    )
    db.add(bot)
    await db.commit()
    # Show the plaintext key exactly once, on the detail page after the redirect.
    request.session[f"fresh_bot_key_{bot.id}"] = key
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{bot_id}", response_class=HTMLResponse)
async def bot_detail(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    bot = await _owned_bot(db, user, bot_id)
    # Shown once, right after issue/reissue. We store only the lookup hash, so we
    # cannot show the key again — and we never regenerate it on a plain visit.
    fresh_key = request.session.pop(f"fresh_bot_key_{bot.id}", None)
    return templates.TemplateResponse(
        request,
        "bots/detail.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "bot": bot,
            "fresh_key": fresh_key,
            "games": await _bot_games(db, bot),
            "base_url": settings.base_url,
        },
    )


@router.post("/{bot_id}/reissue")
async def reissue_key(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    """Issue a fresh key, invalidating the old one immediately. Allowed any time."""
    bot = await _owned_bot(db, user, bot_id)
    key = generate_bot_key()
    bot.key_lookup = bot_key_lookup(key)
    bot.key_hint = bot_key_hint(key)
    await db.commit()
    request.session[f"fresh_bot_key_{bot.id}"] = key
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{bot_id}/pause")
async def pause_bot(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    bot = await _owned_bot(db, user, bot_id)
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
):
    bot = await _owned_bot(db, user, bot_id)
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
):
    bot = await _owned_bot(db, user, bot_id)
    # Players reference the bot (FK, not null). Only a bot with no game history
    # can be deleted; otherwise its player rows would be orphaned.
    has_players = (
        await db.execute(select(Player.id).where(Player.bot_id == bot.id).limit(1))
    ).first()
    if has_players is not None:
        raise HTTPException(
            409,
            detail="This bot has game history and can't be deleted. Pause it instead.",
        )
    await db.delete(bot)
    await db.commit()
    return RedirectResponse(url="/me/bots", status_code=status.HTTP_303_SEE_OTHER)
