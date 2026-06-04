"""Credential and runtime setup actions for bots."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import RedirectResponse

from app.deps import DbSession, require_user
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import BotProvider
from app.models.user import User
from app.routes.bots_web_support import get_owned_bot

router = APIRouter()


@router.post("/{bot_id}/reissue")
async def reissue_key(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    """Issue a fresh key with a graceful overlap. Allowed any time."""
    bot = await get_owned_bot(db, user, bot_id)
    key = generate_bot_key()
    if bot.prev_key_lookup is None:
        bot.prev_key_lookup = bot.key_lookup
    bot.key_lookup = bot_key_lookup(key)
    bot.key_hint = bot_key_hint(key)
    await db.commit()
    request.session[f"fresh_bot_key_{bot.id}"] = key
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{bot_id}/revoke")
async def revoke_and_reissue(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    """Issue a fresh key and kill every old key immediately."""
    bot = await get_owned_bot(db, user, bot_id)
    key = generate_bot_key()
    bot.key_lookup = bot_key_lookup(key)
    bot.key_hint = bot_key_hint(key)
    bot.prev_key_lookup = None
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
) -> RedirectResponse:
    """Save the bot provider and optional CLI-managed model."""
    bot = await get_owned_bot(db, user, bot_id)
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
    return RedirectResponse(
        url=f"/me/bots/{bot.id}#setup", status_code=status.HTTP_303_SEE_OTHER
    )
