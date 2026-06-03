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
from sqlalchemy.exc import IntegrityError

from app.broadcast import subscribe
from app.config import settings
from app.deps import DbSession, require_user
from app.engine.bot_activity import (
    bot_channel,
    compute_bot_health,
    compute_onboarding_status,
)
from app.engine.sim_presets import build_sim_bot_name, sim_presets
from app.engine.sims import pack_profile_choices, resolve_profile_choice
from app.engine.sims.strategies import normalize_strategy_name
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotKind, BotProvider, BotStatus
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.user import User
from app.templating import templates

router = APIRouter(prefix="/me/bots", tags=["bots"])
logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]{1,120}$")


def _is_admin(user: User) -> bool:
    return user.email.lower() in settings.admin_emails_set


async def _owned_bot(db: DbSession, user: User, bot_id: int) -> Bot:
    bot = (
        await db.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id))
    ).scalar_one_or_none()
    if bot is None:
        raise HTTPException(404, detail="Bot not found.")
    return bot


def _archived_name(base: str, archived_at: datetime, extra: str = "") -> str:
    """The renamed-on-archive name, e.g. ``Atlas (archived 2026-05-31 14:22)``.

    Stamping the archived copy frees the original name for reuse without
    touching the unique ``(user_id, name)`` constraint. The base is truncated so
    the result fits the 120-char ``name`` column. ``extra`` carries the bot id
    as a tiebreaker if the same name is archived twice within the same minute.
    """
    suffix = f" (archived {archived_at:%Y-%m-%d %H:%M}{extra})"
    return f"{base[: 120 - len(suffix)]}{suffix}"


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


async def _ensure_preset_sims(db: DbSession, user: User) -> None:
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
    used_names = {bot.name for bot in existing}
    created = False
    for preset in presets:
        if preset.id in by_profile:
            continue
        name = build_sim_bot_name(preset.name, used_names=used_names)
        used_names.add(name)
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


@router.get("", response_class=HTMLResponse)
async def list_bots(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    await _ensure_preset_sims(db, user)
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
            "games": await _bot_games(db, b),
            "health": await compute_bot_health(db, b),
        }
        for b in bots
        if b.kind != BotKind.SIM
    ]
    sim_rows = [
        {
            "bot": b,
            "games": await _bot_games(db, b),
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
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
    kind: Annotated[str, Form()] = "external",
    sim_profile_id: Annotated[str | None, Form()] = None,
    sim_strategy: Annotated[str | None, Form()] = None,
    sim_truthfulness: Annotated[int | None, Form()] = None,
    sim_trust_model: Annotated[str | None, Form()] = None,
    sim_seed: Annotated[int | None, Form()] = None,
):
    name = name.strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(
            400, detail="Bot name must be 1–120 letters, numbers, spaces, _ or -."
        )
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
    # Whether the bot has ever been in a game. Drives the Delete confirm copy:
    # history → archived (kept), no history → permanently deleted.
    has_history = (
        await db.execute(select(Player.id).where(Player.bot_id == bot.id).limit(1))
    ).first() is not None
    return templates.TemplateResponse(
        request,
        "bots/detail.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "bot": bot,
            "fresh_key": fresh_key,
            "games": await _bot_games(db, bot),
            "has_history": has_history,
            "base_url": settings.base_url,
            "onboarding": await compute_onboarding_status(db, bot),
            "health": await compute_bot_health(db, bot),
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


@router.get("/{bot_id}/health-badge", response_class=HTMLResponse)
async def bot_health_badge_fragment(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    """The health-badge fragment, polled by HTMX (every 15s) so the indicator
    stays live without a page reload. Owner-scoped (`_owned_bot` 404s for anyone
    else) and carries no secret — only the derived badge state.
    """
    bot = await _owned_bot(db, user, bot_id)
    return templates.TemplateResponse(
        request,
        "bots/_health_badge.html",
        {"health": await compute_bot_health(db, bot)},
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
    """Issue a fresh key with a graceful overlap. Allowed any time.

    The OLD key keeps working until the new one is first used (then require_bot
    retires it), so reconnecting never knocks a still-running bot offline. For a
    leaked key use ``/revoke`` instead, which cuts the old key off immediately.

    Double-reissue safety: if a previous reissue is still pending (its new key was
    never used, so prev_key_lookup is already set), keep that still-valid old key
    and only replace the unused pending key — the key the bot may actually be
    running on is never orphaned.
    """
    bot = await _owned_bot(db, user, bot_id)
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
):
    """Issue a fresh key and kill every old key IMMEDIATELY (no overlap).

    For the leaked-key case: any AI still using an old code stops working at once
    and must paste the new setup message. For a routine reconnect, prefer
    ``/reissue``, which doesn't interrupt a running bot.
    """
    bot = await _owned_bot(db, user, bot_id)
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
            400, detail="Bot name must be 1–120 letters, numbers, spaces, _ or -."
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
    # Players reference the bot (FK, not null). A bot that has any game history
    # can't be hard-deleted without orphaning those rows, so soft-delete it:
    # mark it archived and pause it. Archived bots are hidden from the owner's
    # lists, rejected from new games, and their key stops authenticating — so a
    # bot that's mid-game simply goes silent and the game's default-turn
    # protocol covers its missing moves. A bot that never played has no rows to
    # preserve, so it's hard-deleted.
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
        # Free the original name for reuse by stamping the archived copy. The
        # bot id is appended only in the rare case two same-named bots are
        # archived within the same minute, which would otherwise collide.
        stamped = _archived_name(bot.name, now)
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
        bot.name = _archived_name(bot.name, now, f" #{bot.id}") if clash else stamped
    else:
        await db.delete(bot)
    await db.commit()
    return RedirectResponse(url="/me/bots", status_code=status.HTTP_303_SEE_OTHER)
