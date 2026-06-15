"""Game-admin HTML pages — per-game match management."""

from datetime import datetime, timezone
from typing import Annotated
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.deps import DbSession, require_game_admin
from app.engine.bot_presets import bot_preset_by_id
from app.engine.match_creation import create_match
from app.engine.match_deletion import cancel_match
from app.engine.scheduler import start_game
from app.engine.bots import validate_bot_profile_fields
from app.engine.bots.roster import PACKS, PERSONALITIES, BOT_NAME_POOL
from app.engine.bots.seating import BotSeatingError, add_bots_to_game
from app.engine.state_machine import TransitionError
from app.games import GameError, get as get_game_module, known_types
from app.games.base import GameConfig
from app.models.game_state import MatchState
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User
from app.routes.web_support import _seated_player_count
from app.templating import templates

router = APIRouter(prefix="/games/{game}/admin", tags=["game-admin"])


async def _load_game_match_or_404(db, game: str, match_id: str) -> Match:
    """Load a match and verify it belongs to this game; 404 if not."""
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None or g.game != game:
        raise HTTPException(404)
    return g


def _game_defaults(game: str) -> GameConfig | SimpleNamespace:
    if game in known_types():
        return get_game_module(game).config_defaults()
    return SimpleNamespace(
        min_players=3,
        max_players=6,
        per_turn_deadline_seconds=30,
        total_rounds=64,
        turns_per_round=256,
    )


@router.get("/", response_class=HTMLResponse)
async def game_admin_dashboard(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
):
    all_matches = (
        (
            await db.execute(
                select(Match)
                .where(Match.game == game)
                .order_by(Match.scheduled_start.desc())
            )
        )
        .scalars()
        .all()
    )
    active, scheduled, completed = [], [], []
    for m in all_matches:
        view = {
            "id": m.id,
            "name": m.name,
            "scheduled_start": m.scheduled_start,
            "current_round": m.current_round,
            "total_rounds": m.total_rounds,
            "state": m.state,
            "player_count": await _seated_player_count(db, m.id),
        }
        if m.state == GameState.ACTIVE:
            active.append(view)
        elif m.state in (GameState.SCHEDULED, GameState.REGISTERING):
            scheduled.append(view)
        else:
            completed.append(view)
    return templates.TemplateResponse(
        request,
        "game_admin/dashboard.html",
        {
            "user": user,
            "is_admin": True,
            "game_slug": game,
            "active_games": active,
            "scheduled_games": scheduled,
            "completed_games": completed,
        },
    )


@router.get("/matches/new", response_class=HTMLResponse)
async def create_match_form(
    game: Annotated[str, Path()],
    request: Request,
    user: Annotated[User, Depends(require_game_admin)],
):
    try:
        module = get_game_module(game)
    except GameError as exc:
        raise HTTPException(404, detail="Game not found.") from exc
    return templates.TemplateResponse(
        request,
        "game_admin/create_match.html",
        {
            "user": user,
            "is_admin": True,
            "game_slug": game,
            "defaults": module.config_defaults(),
            "error": None,
        },
    )


@router.post("/matches/new")
async def create_match_submit(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
    name: Annotated[str, Form()],
    scheduled_start: Annotated[str, Form()],
    min_players: Annotated[int, Form()] = 6,
    max_players: Annotated[int, Form()] = 20,
    per_turn_deadline_seconds: Annotated[int, Form()] = 60,
    total_rounds: Annotated[int, Form()] = 7,
    turns_per_round: Annotated[int, Form()] = 7,
    wild_ones: Annotated[str | None, Form()] = None,
    dice_per_player: Annotated[int, Form()] = 5,
):
    def _error(msg: str):
        return templates.TemplateResponse(
            request,
            "game_admin/create_match.html",
            {
                "user": user,
                "is_admin": True,
                "game_slug": game,
                "defaults": _game_defaults(game),
                "error": msg,
            },
            status_code=400,
        )

    if game not in known_types():
        return _error(f"Unknown game type {game!r}.")
    cfg = _game_defaults(game)
    try:
        when = datetime.fromisoformat(scheduled_start.replace("Z", "+00:00"))
    except ValueError:
        return _error("Could not read the start time. Please pick a date and time.")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when <= datetime.now(timezone.utc):
        return _error("Start time must be in the future.")
    if not (cfg.min_players <= min_players <= cfg.max_players):
        return _error(f"Player counts must be {cfg.min_players} to {cfg.max_players}.")
    if not (cfg.min_players <= max_players <= cfg.max_players):
        return _error(f"Player counts must be {cfg.min_players} to {cfg.max_players}.")
    if min_players > max_players:
        return _error("Min players cannot be greater than max players.")
    if not (3 <= total_rounds <= 20):
        return _error("Total rounds must be 3 to 20.")
    if not (3 <= turns_per_round <= 20):
        return _error("Turns per round must be 3 to 20.")

    g = await create_match(
        db,
        game=game,
        name=name,
        scheduled_start=when,
        min_players=min_players,
        max_players=max_players,
        per_turn_deadline_seconds=per_turn_deadline_seconds,
        total_rounds=total_rounds,
        turns_per_round=turns_per_round,
        state=GameState.REGISTERING,
        created_by_user_id=user.id,
        commit=False,
    )
    db.add(
        MatchState(
            match_id=g.id,
            state_json={
                "config": {
                    "wild_ones": wild_ones is not None,
                    "dice_per_player": dice_per_player,
                }
            },
        )
    )
    await db.commit()
    return RedirectResponse(
        url=f"/games/{game}/admin", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/matches/{match_id}", response_class=HTMLResponse)
async def game_admin_match_detail(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
    added: int | None = None,
):
    g = await _load_game_match_or_404(db, game, match_id)
    players = (
        (await db.execute(select(Player).where(Player.match_id == match_id))).scalars().all()
    )
    agents_by_id = (
        {
            agent.id: agent
            for agent in (
                (
                    await db.execute(
                        select(Agent).where(Agent.id.in_([p.agent_id for p in players]))
                    )
                )
                .scalars()
                .all()
            )
        }
        if players
        else {}
    )
    version_ids = [p.agent_version_id for p in players if p.agent_version_id is not None]
    versions_by_id = (
        {
            v.id: v
            for v in (
                (
                    await db.execute(
                        select(AgentVersion).where(AgentVersion.id.in_(version_ids))
                    )
                )
                .scalars()
                .all()
            )
        }
        if version_ids
        else {}
    )
    player_views = []
    for p in players:
        agent = agents_by_id.get(p.agent_id)
        version = (
            versions_by_id.get(p.agent_version_id)
            if p.agent_version_id is not None
            else None
        )
        is_bot = agent is not None and agent.kind == AgentKind.BOT
        personality = (
            (agent.bot_strategy or "").replace("_", " ").title()
            if is_bot and agent is not None
            else ""
        )
        player_views.append(
            {
                "agent_id": p.seat_name,
                "total_round_wins": p.total_round_wins,
                "total_round_score": p.total_round_score,
                "strategy": version.strategy_text if version else (agent.bot_strategy if agent else ""),
                "is_bot": is_bot,
                "personality": personality,
            }
        )
    can_add_bots = g.state in (GameState.SCHEDULED, GameState.REGISTERING)
    return templates.TemplateResponse(
        request,
        "game_admin/match_detail.html",
        {
            "user": user,
            "is_admin": True,
            "game_slug": game,
            "match": g,
            "players": player_views,
            "can_add_bots": can_add_bots,
            "added": added,
        },
    )


async def _render_add_bots(
    request: Request,
    db,
    user: User,
    game: str,
    match: Match,
    *,
    error: str | None = None,
    prefill: list[tuple[str, str]] | None = None,
    status_code: int = 200,
):
    existing = list(
        (
            await db.execute(
                select(Player.seat_name).where(
                    Player.match_id == match.id, Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    can_add = match.state in (GameState.SCHEDULED, GameState.REGISTERING)
    bots_data = {
        "maxPlayers": match.max_players,
        "currentCount": len(existing),
        "existing": existing,
        "names": list(BOT_NAME_POOL),
        "personalities": [
            {"id": p.id, "label": p.label, "description": p.description, "lean": p.lean}
            for p in PERSONALITIES
        ],
        "packs": [
            {
                "id": pk.id,
                "label": pk.label,
                "description": pk.description,
                "strategies": list(pk.strategies),
            }
            for pk in PACKS
        ],
        "prefill": [{"name": n, "strategy": s} for n, s in (prefill or [])],
    }
    return templates.TemplateResponse(
        request,
        "game_admin/add_bots.html",
        {
            "user": user,
            "is_admin": True,
            "game_slug": game,
            "match": match,
            "personalities": PERSONALITIES,
            "packs": PACKS,
            "can_add": can_add,
            "current_count": len(existing),
            "error": error,
            "bots_data": bots_data,
        },
        status_code=status_code,
    )


@router.get("/matches/{match_id}/bots", response_class=HTMLResponse)
async def add_bots_form(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
):
    g = await _load_game_match_or_404(db, game, match_id)
    return await _render_add_bots(request, db, user, game, g)


@router.post("/matches/{match_id}/bots")
async def add_bots_submit(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
    seat_name: Annotated[list[str] | None, Form()] = None,
    seat_strategy: Annotated[list[str] | None, Form()] = None,
):
    g = await _load_game_match_or_404(db, game, match_id)
    if g.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        return await _render_add_bots(
            request,
            db,
            user,
            game,
            g,
            error="Bots can only be added before a match starts.",
            status_code=409,
        )
    names = [n.strip() for n in (seat_name or [])]
    strategies = [s.strip() for s in (seat_strategy or [])]
    if len(names) != len(strategies):
        return await _render_add_bots(
            request,
            db,
            user,
            game,
            g,
            error="Something went wrong reading the roster. Please try again.",
            status_code=400,
        )
    seats = list(zip(names, strategies))
    # Validate each bot configuration before seating so the user gets a clear
    # form error instead of a late failure inside add_bots_to_game.  We only
    # validate bot-kind entries (this form exclusively creates bots, so the
    # guard is always true, but the check is explicit for clarity).
    for seat_name_val, seat_strategy_val in seats:
        preset = bot_preset_by_id(seat_strategy_val)
        if preset is None:
            # Unknown personality ID — build a legible error before seating.
            return await _render_add_bots(
                request,
                db,
                user,
                game,
                g,
                error=f"Unknown bot strategy {seat_strategy_val!r} for bot {seat_name_val!r}.",
                prefill=seats,
                status_code=400,
            )
        try:
            validate_bot_profile_fields(
                kind=AgentKind.BOT,
                bot_strategy=preset.strategy,
                bot_truthfulness=preset.truthfulness,
                bot_trust_model=preset.trust_model,
                bot_seed=preset.seed_offset,  # non-None int; seating overwrites with agent.id
                bot_version="v1",
            )
        except ValueError as exc:
            return await _render_add_bots(
                request,
                db,
                user,
                game,
                g,
                error=f"Invalid bot configuration for {seat_name_val!r}: {exc}",
                prefill=seats,
                status_code=400,
            )
    try:
        created = await add_bots_to_game(db, g, seats)
    except BotSeatingError as exc:
        return await _render_add_bots(
            request, db, user, game, g, error=str(exc), prefill=seats, status_code=400
        )
    return RedirectResponse(
        url=f"/games/{game}/admin/matches/{match_id}?added={len(created)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/matches/{match_id}/start")
async def game_admin_start_match(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
):
    g = await _load_game_match_or_404(db, game, match_id)
    try:
        await start_game(db, g)
    except TransitionError:
        raise HTTPException(409, detail=f"Cannot start a match in state {g.state.value}.")
    return RedirectResponse(
        url=f"/games/{game}/admin/matches/{match_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/matches/{match_id}/cancel")
async def game_admin_cancel_match(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
):
    g = await _load_game_match_or_404(db, game, match_id)
    if g.state == GameState.ACTIVE:
        raise HTTPException(409, detail="Match already started.")
    if g.state in (GameState.COMPLETED, GameState.CANCELLED):
        raise HTTPException(409, detail="Match already ended.")
    await cancel_match(db, g)
    return RedirectResponse(
        url=f"/games/{game}/admin",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/prompts", response_class=HTMLResponse)
async def game_admin_prompts(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_game_admin)],
):
    prompts = (
        (
            await db.execute(
                select(Player.match_id, Player.seat_name, AgentVersion)
                .join(Match, Match.id == Player.match_id)
                .join(AgentVersion, AgentVersion.id == Player.agent_version_id)
                .where(Match.game == game)
                .order_by(AgentVersion.created_at.desc())
            )
        ).all()
    )
    rows = [
        {
            "match_id": match_id,
            "agent_id": seat_name,
            "created_at": version.created_at,
            "is_default": version.version_no == 1,
            "prompt": version.strategy_text,
        }
        for match_id, seat_name, version in prompts
    ]
    return templates.TemplateResponse(
        request,
        "game_admin/prompts.html",
        {"user": user, "is_admin": True, "game_slug": game, "rows": rows},
    )
