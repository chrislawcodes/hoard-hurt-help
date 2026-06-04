"""Guide, runner download, join, and player dashboard web routes."""

import random
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession, get_current_user, require_user
from app.engine.scheduler import start_game
from app.games import get as get_game_module
from app.models.bot import Bot, BotKind
from app.models.match import Match, GameState, MatchKind
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.user import User
from app.request_logging import set_request_trace_context
from app.routes.web_support import (
    _GENERAL_NAMES,
    _game_theme,
    _is_admin,
    _load_match_or_404,
    _load_owned_player_match_or_404,
    _player_count,
    _redirect_to_match,
    _redirect_if_game_slug_mismatch,
)
from app.templating import templates

router = APIRouter(tags=["web"])

_DOCS_DIR = FsPath("docs")
_GUIDE_NAME = re.compile(r"^[a-z0-9-]+$")
_BOT_LIVE_WINDOW = timedelta(seconds=90)


def _is_warm(bot: Bot) -> bool:
    """True if this bot's runner contacted the server in the last 90 seconds."""
    ls = bot.last_seen_at
    if ls is None:
        return False
    aware = ls if ls.tzinfo is not None else ls.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - aware <= _BOT_LIVE_WINDOW


@router.get("/guide/{name}", response_class=HTMLResponse)
async def guide(name: Annotated[str, Path()], request: Request, db: DbSession):
    """Render a setup doc from docs/<name>.md inside the site chrome."""
    if not _GUIDE_NAME.match(name):
        raise HTTPException(404)
    path = _DOCS_DIR / f"{name}.md"
    if not path.is_file():
        raise HTTPException(404)
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        "guide.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "title": name.replace("-", " ").title(),
            "body": path.read_text(encoding="utf-8"),
        },
    )


_RUNNER_PATH = FsPath("scripts/agentludum_bot.py")


@router.get("/agentludum_bot.py", include_in_schema=False)
async def runner_script() -> FileResponse:
    """Serve the bot runner so the one-line `curl` in the setup message fetches it.

    Single source of truth: this streams the repo's scripts/agentludum_bot.py, so
    the downloaded runner always matches this server's version.
    """
    if not _RUNNER_PATH.is_file():
        raise HTTPException(404)
    return FileResponse(
        _RUNNER_PATH, media_type="text/x-python", filename="agentludum_bot.py"
    )


# Chained-session agent runner. ONE script now drives every CLI provider — it
# reads the bot's configured provider from the server and calls the matching CLI
# (claude/codex/gemini). The old per-provider filenames are kept as aliases so an
# older setup message still fetches a working runner; they all serve the same file.
# Allowlisted by exact filename below.
_UNIFIED_RUNNER = FsPath("scripts/agentludum_agent.py")
_AGENT_RUNNERS: dict[str, FsPath] = {
    "agentludum_agent.py": _UNIFIED_RUNNER,
    "agentludum_agent_codex.py": _UNIFIED_RUNNER,
    "agentludum_agent_gemini.py": _UNIFIED_RUNNER,
}


@router.get("/runners/{name}", include_in_schema=False)
async def agent_runner_script(name: Annotated[str, Path()]) -> FileResponse:
    """Serve a chained-session agent runner so the setup `curl` fetches it.

    Allowlisted by exact filename — the path never comes from the request, so
    there's no traversal surface. Single source of truth: this streams the
    repo's scripts/<name>, so the downloaded runner always matches this server.
    """
    path = _AGENT_RUNNERS.get(name)
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/x-python", filename=name)


@router.get("/games/{match_id}/join", response_class=HTMLResponse)
async def legacy_join_form_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/join")


@router.post("/games/{match_id}/join", include_in_schema=False)
async def legacy_join_submit_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return RedirectResponse(
        url=(await _redirect_to_match(db, match_id, suffix="/join")).headers["location"],
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )


@router.get("/games/{game}/matches/{match_id}/join", response_class=HTMLResponse)
async def join_form(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    user = await get_current_user(request, db)
    if user is None:
        # Send through OAuth, returning back to this URL.
        return RedirectResponse(
            url=f"/auth/google/login?next=/games/{game}/matches/{match_id}/join",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    set_request_trace_context(request, match_id=match_id, stage="join_form")
    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(match, game, "/join"):
        return redirect

    # Entry is "pick one of your bots" — no per-game key is issued. The bot's
    # stable key was shown once when it was created (see /me/bots). Archived
    # (deleted) bots are excluded — they can't enter games.
    all_bots = (
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
    # Only show external agents that are currently connected; sims are always ready.
    connected_agents = [b for b in all_bots if b.kind == BotKind.EXTERNAL and _is_warm(b)]
    sims = [b for b in all_bots if b.kind == BotKind.SIM]

    module = get_game_module(match.game)
    presets = [asdict(p) for p in module.strategy_presets()]
    default_preset_id = presets[0]["id"] if presets else ""
    strategy_prompt = presets[0]["prompt"] if presets else module.default_strategy()
    default_display_name = random.choice(_GENERAL_NAMES)
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": match,
            "game_theme": _game_theme(match),
            "player_count": await _player_count(db, match.id),
            "connected_agents": connected_agents,
            "sims": sims,
            "any_bots": bool(all_bots),
            "presets": presets,
            "default_preset_id": default_preset_id,
            "strategy_prompt": strategy_prompt,
            "default_display_name": default_display_name,
            "base_url": settings.base_url,
            "error": None,
        },
    )


@router.post("/games/{game}/matches/{match_id}/join")
async def join_submit(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    bot_id: Annotated[int, Form()],
    display_name: Annotated[str, Form()],
    strategy_prompt: Annotated[str, Form()] = "",
):
    """Enter one of the user's bots into a game. No credential is issued."""
    set_request_trace_context(
        request, match_id=match_id, stage="join_submit", bot_id=bot_id, display_name=display_name
    )
    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(
        match,
        game,
        "/join",
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    ):
        return redirect
    if match.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Match not open for registration.")

    bot = (
        await db.execute(
            select(Bot).where(
                Bot.id == bot_id,
                Bot.user_id == user.id,
                Bot.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if bot is None:
        raise HTTPException(404, detail="Bot not found.")

    # Validate entry: name shape, one player per (bot, game), unique name, capacity.
    name_ok = bool(re.fullmatch(r"[a-zA-Z0-9_]{1,32}", display_name))
    already_in = (
        await db.execute(
            select(Player).where(
                Player.bot_id == bot.id,
                Player.match_id == match.id,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    name_taken = (
        await db.execute(
            select(Player).where(
                Player.match_id == match.id,
                Player.agent_id == display_name,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    count = await _player_count(db, match.id)

    error: str | None = None
    code = status.HTTP_400_BAD_REQUEST
    if not name_ok:
        error = "Name must be 1–32 letters, numbers, or underscores."
    elif already_in is not None:
        error, code = "That bot is already in this game.", status.HTTP_409_CONFLICT
    elif name_taken is not None:
        error = "That display name is already taken in this game."
    elif count >= match.max_players:
        error, code = "Match is full.", status.HTTP_409_CONFLICT
    if error is not None:
        all_bots_err = (
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
        presets = [asdict(p) for p in get_game_module(match.game).strategy_presets()]
        return templates.TemplateResponse(
            request,
            "join.html",
            {
                "user": user,
                "is_admin": _is_admin(user),
                "game": match,
                "game_theme": _game_theme(match),
                "player_count": count,
                "connected_agents": [b for b in all_bots_err if b.kind == BotKind.EXTERNAL],
                "sims": [b for b in all_bots_err if b.kind == BotKind.SIM],
                "any_bots": bool(all_bots_err),
                "presets": presets,
                "default_preset_id": presets[0]["id"] if presets else "",
                "strategy_prompt": strategy_prompt,
                "default_display_name": display_name,
                "base_url": settings.base_url,
                "error": error,
            },
            status_code=code,
        )

    if bot.provider:
        _model_label = bot.provider.value + (f"/{bot.model}" if bot.model else "")
    else:
        _model_label = None
    player = Player(
        match_id=match.id,
        user_id=bot.user_id,
        bot_id=bot.id,
        agent_id=display_name,
        model_self_report=_model_label,
    )
    db.add(player)
    await db.flush()
    # Seed the player's per-game strategy from what they submitted at entry (a
    # preset they picked or text they wrote); blank falls back to the game's
    # default. Copy-at-entry: later edits on the player page don't rewrite this.
    seed = strategy_prompt.strip() or get_game_module(match.game).default_strategy()
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=seed,
            is_default=False,
        )
    )
    await db.commit()

    if match.match_kind == MatchKind.PRACTICE_ARENA.value:
        await start_game(db, match)

    return RedirectResponse(
        url=f"/games/{match.game}/matches/{match.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/me/matches", response_class=HTMLResponse)
async def my_matches(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    players = (
        (await db.execute(select(Player).where(Player.user_id == user.id))).scalars().all()
    )
    games = []
    for p in players:
        g = (await db.execute(select(Match).where(Match.id == p.match_id))).scalar_one()
        games.append(
            {
                "id": g.id,
                "name": g.name,
                "state": g.state,
                "agent_id": p.agent_id,
                "player_id": p.id,
                "game_type": g.game,
                "watch_url": f"/games/{g.game}/matches/{g.id}",
            }
        )
    return templates.TemplateResponse(
        request,
        "my_matches.html",
        {"user": user, "is_admin": _is_admin(user), "games": games},
    )


@router.get("/me/games", response_class=HTMLResponse, include_in_schema=False)
async def my_games_redirect(request: Request):
    return RedirectResponse(url="/me/matches", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/me/players/{player_id}", response_class=HTMLResponse)
async def player_dashboard(
    player_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    saved: bool = False,
):
    player, game = await _load_owned_player_match_or_404(
        db,
        player_id,
        user.id,
        missing_detail="Bot slot not found.",
    )
    presets = [asdict(p) for p in get_game_module(game.game).strategy_presets()]

    latest_prompt = (
        await db.execute(
            select(StrategyPrompt)
            .where(StrategyPrompt.player_id == player.id)
            .order_by(StrategyPrompt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # The agent key is shown exactly once, right after it is issued — on join or
    # on an explicit re-issue (see reissue_agent_key). We only ever store the
    # argon2 hash, so we cannot show the key again on later visits. Crucially, we
    # do NOT regenerate the key on a plain dashboard visit: doing so silently
    # invalidated the key a bot was already configured with.
    fresh_key = request.session.pop(f"fresh_key_{player.id}", None)

    selected_ai = request.session.pop(f"ai_type_{player.id}", None)
    pre_game = game.state in (GameState.SCHEDULED, GameState.REGISTERING)

    return templates.TemplateResponse(
        request,
        "connection.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": game,
            "game_theme": _game_theme(game),
            "player": player,
            "agent_key": fresh_key,
            "strategy": latest_prompt.prompt_text if latest_prompt else "",
            "base_url": settings.base_url,
            "selected_ai": selected_ai,
            "presets": presets,
            "just_saved": saved,
            "can_edit_strategy": game.state != GameState.ACTIVE
            and game.state != GameState.COMPLETED,
            "can_leave": pre_game,
            "pre_game": pre_game,
        },
    )


# Key reissue moved to the bot level (POST /me/bots/{bot_id}/reissue) and is
# allowed at any time — see app/routes/bots_web.py. There is no per-player key.


@router.post("/me/players/{player_id}/strategy")
async def update_strategy(
    player_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    strategy_prompt: Annotated[str, Form()],
):
    player, game = await _load_owned_player_match_or_404(db, player_id, user.id)
    if game.state in (GameState.ACTIVE, GameState.COMPLETED):
        raise HTTPException(409, detail="Strategy locked after game starts.")
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=strategy_prompt,
            is_default=False,
        )
    )
    await db.commit()
    return RedirectResponse(
        url=f"/me/players/{player.id}?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/me/players/{player_id}/leave")
async def web_leave(
    player_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    player, game = await _load_owned_player_match_or_404(db, player_id, user.id)
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Cannot leave after start.")
    player.left_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/me/matches", status_code=status.HTTP_303_SEE_OTHER)
