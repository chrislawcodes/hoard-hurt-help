"""Dashboard and setup routes for the self-serve bot panel."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.responses import Response

from app.config import settings
from app.deps import DbSession, require_user, require_user_with_handle
from app.engine.bot_activity import compute_bot_health, compute_onboarding_status
from app.engine.sims import pack_profile_choices, resolve_profile_choice
from app.engine.sims.strategies import normalize_strategy_name
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotKind
from app.models.player import Player
from app.models.user import User
from app.routes.bots_web_support import (
    bot_game_rows,
    ensure_preset_sim_bots,
    get_owned_bot,
    validate_bot_name,
)
from app.routes.web_support import _is_admin
from app.templating import templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def list_bots(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    await ensure_preset_sim_bots(db, user)
    bots = (
        (
            await db.execute(
                select(Bot)
                .where(Bot.user_id == user.id, Bot.archived_at.is_(None))
                .order_by(Bot.name)
            )
        )
        .scalars()
        .all()
    )
    rows = [
        {
            "bot": b,
            "games": await bot_game_rows(db, b),
            "health": await compute_bot_health(db, b),
        }
        for b in bots
        if b.kind != BotKind.SIM
    ]
    sim_rows = [
        {
            "bot": b,
            "games": await bot_game_rows(db, b),
            "health": await compute_bot_health(db, b),
        }
        for b in bots
        if b.kind == BotKind.SIM
    ]
    return templates.TemplateResponse(
        request,
        "bots/list.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "rows": rows,
            "sim_rows": sim_rows,
            "sim_profile_choices": pack_profile_choices(include_hidden=_is_admin(user)),
        },
    )


@router.post("")
async def create_bot(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    name: Annotated[str, Form()],
    kind: Annotated[str, Form()] = "external",
    sim_profile_id: Annotated[str | None, Form()] = None,
    sim_strategy: Annotated[str | None, Form()] = None,
    sim_truthfulness: Annotated[int | None, Form()] = None,
    sim_trust_model: Annotated[str | None, Form()] = None,
    sim_seed: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    name = validate_bot_name(name)
    existing = (
        await db.execute(select(Bot).where(Bot.user_id == user.id, Bot.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, detail="You already have a bot with that name.")

    try:
        bot_kind = BotKind(kind.strip().lower() or BotKind.EXTERNAL.value)
    except ValueError:
        raise HTTPException(400, detail="Unknown bot kind.")

    key = generate_bot_key()
    bot = Bot(
        user_id=user.id,
        name=name,
        key_lookup=bot_key_lookup(key),
        key_hint=bot_key_hint(key),
        kind=bot_kind,
    )
    db.add(bot)
    await db.commit()
    if bot.kind == BotKind.SIM:
        allowed_choices = {
            choice.id: choice
            for choice in pack_profile_choices(include_hidden=_is_admin(user))
        }
        if sim_profile_id and sim_profile_id not in allowed_choices:
            raise HTTPException(400, detail="Unknown Sim profile.")
        if sim_profile_id:
            seed_base = sim_seed if sim_seed is not None else bot.id
            try:
                profile = resolve_profile_choice(sim_profile_id, seed_base=seed_base)
            except (KeyError, IndexError, ValueError) as exc:
                raise HTTPException(400, detail="Unknown Sim profile.") from exc
            bot.sim_strategy = normalize_strategy_name(profile.strategy)
            bot.sim_truthfulness = profile.truthfulness
            bot.sim_trust_model = profile.trust_model
            bot.sim_seed = profile.seed
            bot.sim_version = profile.version
            bot.sim_fixture_pack = profile.fixture_pack
        else:
            bot.sim_strategy = normalize_strategy_name(sim_strategy or "coalition_seeker")
            bot.sim_truthfulness = sim_truthfulness if sim_truthfulness is not None else 80
            bot.sim_trust_model = (sim_trust_model or "even").strip().lower()
            bot.sim_seed = sim_seed if sim_seed is not None else bot.id
            bot.sim_version = "v1"
        await db.commit()
    # Show the plaintext key exactly once, on the detail page after the redirect.
    request.session[f"fresh_bot_key_{bot.id}"] = key
    if bot.kind == BotKind.EXTERNAL:
        # Flag so the status fragment can redirect to the lobby on first connect.
        request.session["onboarding_bot_id"] = bot.id
    return RedirectResponse(url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{bot_id}", response_class=HTMLResponse)
async def bot_detail(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Response:
    bot = await get_owned_bot(db, user, bot_id)
    # Shown once, right after issue/reissue. We store only the lookup hash, so we
    # cannot show the key again — and we never regenerate it on a plain visit.
    fresh_key = request.session.pop(f"fresh_bot_key_{bot.id}", None)
    # Whether the bot has ever been in a game. Drives the Delete confirm copy:
    # history → archived (kept), no history → permanently deleted.
    has_history = (
        await db.execute(select(Player.id).where(Player.bot_id == bot.id).limit(1))
    ).first() is not None
    games = await bot_game_rows(db, bot)
    game_count = len(games)
    total_wins = sum(g["total_score"] for g in games)
    avg_wins = round(total_wins / game_count, 1) if game_count else 0
    bot_stats = {"count": game_count, "total_wins": total_wins, "avg_wins": avg_wins}
    return templates.TemplateResponse(
        request,
        "bots/detail.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "bot": bot,
            "fresh_key": fresh_key,
            "games": games,
            "has_history": has_history,
            "base_url": settings.base_url,
            "onboarding": await compute_onboarding_status(db, bot),
            "health": await compute_bot_health(db, bot),
            "bot_stats": bot_stats,
        },
    )
