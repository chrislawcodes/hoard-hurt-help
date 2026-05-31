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
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select

from app.broadcast import subscribe
from app.config import settings
from app.deps import DbSession, require_user
from app.engine.bot_activity import bot_channel, compute_onboarding_status
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotProvider, BotStatus
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
            "onboarding": await compute_onboarding_status(db, bot),
        },
    )


@router.get("/{bot_id}/status", response_class=HTMLResponse)
async def bot_status_fragment(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    """The live onboarding status panel, re-fetched by HTMX on SSE events.

    Owner-scoped (`_owned_bot` 404s for anyone else) and carries no secret — only
    the derived state, so connection status never leaks.
    """
    bot = await _owned_bot(db, user, bot_id)
    return templates.TemplateResponse(
        request,
        "bots/_status.html",
        {"bot": bot, "onboarding": await compute_onboarding_status(db, bot)},
    )


@router.get("/{bot_id}/stream")
async def bot_stream(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> StreamingResponse:
    """Per-bot SSE stream of onboarding events (`connected`, `moved`).

    Owner-scoped. Mirrors the spectator stream but keyed to this bot's channel,
    so only the owner can observe their bot's connection status.
    """
    await _owned_bot(db, user, bot_id)

    async def event_gen():
        async for msg in subscribe(bot_channel(bot_id)):
            yield msg

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
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


@router.post("/{bot_id}/set-model")
async def set_bot_model(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    provider: Annotated[str, Form()],
    model: Annotated[str | None, Form()] = None,
):
    """Save the bot's provider and model. For CLI providers (claude/gemini/openai)
    a specific model may optionally be set. For MCP providers (hermes/openclaw)
    the model is managed by the agent itself, so we clear it."""
    bot = await _owned_bot(db, user, bot_id)
    if provider == "":
        bot.provider = None
        bot.model = None
    else:
        try:
            bot.provider = BotProvider(provider)
        except ValueError:
            raise HTTPException(400, detail=f"Unknown provider {provider!r}.")
        cli_providers = {BotProvider.CLAUDE, BotProvider.GEMINI, BotProvider.OPENAI}
        bot.model = (model or None) if bot.provider in cli_providers else None
    await db.commit()
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{bot_id}/rename")
async def rename_bot(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
):
    """Rename a bot. The connection code is unaffected — only the label changes."""
    bot = await _owned_bot(db, user, bot_id)
    name = name.strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(
            400, detail="Bot name must be 1–64 letters, numbers, spaces, _ or -."
        )
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
