"""Admin HTML pages."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from app.deps import DbSession, require_admin
from app.engine.scheduler import registry, start_game
from app.engine.state_machine import TransitionError
from app.engine.tokens import generate_game_id
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.strategy_prompt import StrategyPrompt
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["admin"])


async def _player_count(db, game_id: str) -> int:
    return len(
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    all_games = (
        (await db.execute(select(Game).order_by(Game.scheduled_start.desc()))).scalars().all()
    )
    active, scheduled, completed = [], [], []
    for g in all_games:
        view = {
            "id": g.id,
            "name": g.name,
            "scheduled_start": g.scheduled_start.isoformat(),
            "min_players": g.min_players,
            "max_players": g.max_players,
            "state": g.state,
            "player_count": await _player_count(db, g.id),
        }
        if g.state == GameState.ACTIVE:
            active.append(view)
        elif g.state in (GameState.SCHEDULED, GameState.REGISTERING):
            scheduled.append(view)
        else:
            completed.append(view)
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "is_admin": True,
            "active_games": active,
            "scheduled_games": scheduled,
            "completed_games": completed,
        },
    )


@router.get("/admin/incidents", response_class=HTMLResponse)
async def admin_incidents(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    request_id: str | None = None,
):
    stmt = select(RequestIncident).order_by(RequestIncident.created_at.desc()).limit(200)
    if request_id:
        stmt = stmt.where(RequestIncident.request_id == request_id.strip())
    incidents = (await db.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        request,
        "admin/incidents.html",
        {
            "user": user,
            "is_admin": True,
            "incidents": incidents,
            "request_id": request_id or "",
        },
    )


@router.get("/admin/incidents/{incident_id}", response_class=HTMLResponse)
async def admin_incident_detail(
    incident_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    incident = (
        await db.execute(select(RequestIncident).where(RequestIncident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "admin/incident_detail.html",
        {"user": user, "is_admin": True, "incident": incident},
    )


@router.get("/admin/games/new", response_class=HTMLResponse)
async def create_game_form(
    request: Request,
    user: Annotated[User, Depends(require_admin)],
):
    return templates.TemplateResponse(
        request,
        "admin/create_game.html",
        {"user": user, "is_admin": True, "error": None},
    )


@router.post("/admin/games/new")
async def create_game_submit(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    name: Annotated[str, Form()],
    scheduled_start: Annotated[str, Form()],
    min_players: Annotated[int, Form()] = 3,
    max_players: Annotated[int, Form()] = 20,
    per_turn_deadline_seconds: Annotated[int, Form()] = 60,
):
    def _error(msg: str):
        return templates.TemplateResponse(
            request,
            "admin/create_game.html",
            {"user": user, "is_admin": True, "error": msg},
            status_code=400,
        )

    try:
        when = datetime.fromisoformat(scheduled_start.replace("Z", "+00:00"))
    except ValueError:
        return _error("Could not read the start time. Please pick a date and time.")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when <= datetime.now(timezone.utc):
        return _error("Start time must be in the future.")
    if not (3 <= min_players <= 20) or not (3 <= max_players <= 20):
        return _error("Player counts must be 3 to 20.")
    if min_players > max_players:
        return _error("Min players cannot be greater than max players.")

    existing_ids = (await db.execute(select(Game.id))).scalars().all()
    n = max((int(x.split("_")[1]) for x in existing_ids if x.startswith("G_")), default=0) + 1
    g = Game(
        id=generate_game_id(n),
        name=name,
        state=GameState.REGISTERING,
        scheduled_start=when,
        min_players=min_players,
        max_players=max_players,
        per_turn_deadline_seconds=per_turn_deadline_seconds,
    )
    db.add(g)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/games/{game_id}", response_class=HTMLResponse)
async def admin_game_detail(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    players = (
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
    )
    player_views = []
    for p in players:
        prompt = (
            await db.execute(
                select(StrategyPrompt)
                .where(StrategyPrompt.player_id == p.id)
                .order_by(StrategyPrompt.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        player_views.append(
            {
                "agent_id": p.agent_id,
                "total_round_wins": p.total_round_wins,
                "total_round_score": p.total_round_score,
                "strategy": prompt.prompt_text if prompt else "",
            }
        )
    return templates.TemplateResponse(
        request,
        "admin/game_detail.html",
        {"user": user, "is_admin": True, "game": g, "players": player_views},
    )


@router.post("/admin/games/{game_id}/start")
async def admin_start_game(
    game_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    """Force a REGISTERING game to start now (manual override of the auto-start poller)."""
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    try:
        await start_game(db, g)
    except TransitionError:
        raise HTTPException(409, detail=f"Cannot start a game in state {g.state.value}.")
    return RedirectResponse(
        url=f"/admin/games/{game_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/games/{game_id}/delete")
async def admin_delete_game(
    game_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    next: Annotated[str, Form()] = "/admin",
):
    """Permanently delete a game and everything under it. Admin only.

    Deletes in FK-safe order: clear the game's winner pointer, then
    submissions → turns → strategy prompts → players → the game itself.
    Stops the game's loop first if it happens to be running.
    """
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)

    registry.stop(game_id)  # no-op if not running

    # Break the games → players FK so players can be deleted.
    g.winner_player_id = None
    await db.flush()

    turn_ids = (
        (await db.execute(select(Turn.id).where(Turn.game_id == game_id))).scalars().all()
    )
    if turn_ids:
        await db.execute(delete(TurnSubmission).where(TurnSubmission.turn_id.in_(turn_ids)))
    await db.execute(delete(Turn).where(Turn.game_id == game_id))

    player_ids = (
        (await db.execute(select(Player.id).where(Player.game_id == game_id))).scalars().all()
    )
    if player_ids:
        await db.execute(delete(StrategyPrompt).where(StrategyPrompt.player_id.in_(player_ids)))
    await db.execute(delete(Player).where(Player.game_id == game_id))

    await db.execute(delete(Game).where(Game.id == game_id))
    await db.commit()

    # Only redirect to safe local paths.
    target = next if next.startswith("/") else "/admin"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/prompts", response_class=HTMLResponse)
async def admin_prompts(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    prompts = (
        (await db.execute(select(StrategyPrompt).order_by(StrategyPrompt.created_at.desc())))
        .scalars()
        .all()
    )
    players_by_id = {
        p.id: p for p in (await db.execute(select(Player))).scalars().all()
    }
    rows = []
    for pr in prompts:
        player = players_by_id.get(pr.player_id)
        if not player:
            continue
        rows.append(
            {
                "game_id": player.game_id,
                "agent_id": player.agent_id,
                "created_at": pr.created_at.isoformat(),
                "is_default": pr.is_default,
                "prompt": pr.prompt_text,
            }
        )
    return templates.TemplateResponse(
        request,
        "admin/prompts.html",
        {"user": user, "is_admin": True, "rows": rows},
    )
