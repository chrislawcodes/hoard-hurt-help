"""Admin HTML pages."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from app.deps import DbSession, require_admin
from app.engine.scheduler import registry, start_game
from app.engine.sims.roster import PACKS, PERSONALITIES, SIM_NAME_POOL
from app.engine.sims.seating import SimSeatingError, add_sims_to_game
from app.engine.state_machine import TransitionError
from app.engine.tokens import generate_match_id
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.routes.web_support import _load_match_or_404, _seated_player_count
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    all_games = (
        (await db.execute(select(Match).order_by(Match.scheduled_start.desc()))).scalars().all()
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
            "player_count": await _seated_player_count(db, g.id),
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


@router.get("/admin/handles", response_class=HTMLResponse)
async def admin_handles(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    """List users who have a public handle, so an admin can reset a bad one."""
    rows = (
        (
            await db.execute(
                select(User).where(User.handle.is_not(None)).order_by(User.handle)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/handles.html",
        {"user": user, "is_admin": True, "users": rows},
    )


@router.post("/admin/users/{user_id}/handle/reset")
async def admin_reset_handle(
    user_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    """Clear a user's handle. The string is freed immediately; the user picks a
    new one the next time they need it. Identity is keyed on users.id, so all
    leaderboard history is preserved."""
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, detail="User not found.")
    target.handle = None
    target.handle_key = None
    target.handle_changed_at = None
    await db.commit()
    return RedirectResponse(url="/admin/handles", status_code=status.HTTP_303_SEE_OTHER)


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


@router.get("/admin/matches/new", response_class=HTMLResponse)
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


@router.post("/admin/matches/new")
@router.post("/admin/games/new")
async def create_game_submit(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    name: Annotated[str, Form()],
    scheduled_start: Annotated[str, Form()],
    min_players: Annotated[int, Form()] = 6,
    max_players: Annotated[int, Form()] = 20,
    per_turn_deadline_seconds: Annotated[int, Form()] = 60,
    total_rounds: Annotated[int, Form()] = 10,
    turns_per_round: Annotated[int, Form()] = 10,
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
    if not (3 <= total_rounds <= 20):
        return _error("Total rounds must be 3 to 20.")
    if not (3 <= turns_per_round <= 20):
        return _error("Turns per round must be 3 to 20.")

    existing_ids = (await db.execute(select(Match.id))).scalars().all()
    n = max((int(x.split("_")[1]) for x in existing_ids if x.startswith("M_")), default=0) + 1
    g = Match(
        id=generate_match_id(n),
        name=name,
        state=GameState.REGISTERING,
        scheduled_start=when,
        min_players=min_players,
        max_players=max_players,
        per_turn_deadline_seconds=per_turn_deadline_seconds,
        total_rounds=total_rounds,
        turns_per_round=turns_per_round,
    )
    db.add(g)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/matches/{match_id}", response_class=HTMLResponse)
@router.get("/admin/games/{match_id}", response_class=HTMLResponse)
async def admin_game_detail(
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    added: int | None = None,
):
    g = await _load_match_or_404(db, match_id)
    players = (
        (await db.execute(select(Player).where(Player.match_id == match_id))).scalars().all()
    )
    agents_by_id = {
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
    } if players else {}
    # Display the version each player ACTUALLY played (Player.agent_version_id),
    # not the agent's current version — otherwise an admin auditing a finished
    # match sees a strategy that may have been edited after the match.
    version_ids = [p.agent_version_id for p in players if p.agent_version_id is not None]
    versions_by_id = {
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
    } if version_ids else {}
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
                "is_sim": is_bot,
                "personality": personality,
            }
        )
    can_add_sims = g.state in (GameState.SCHEDULED, GameState.REGISTERING)
    return templates.TemplateResponse(
        request,
        "admin/game_detail.html",
        {
            "user": user,
            "is_admin": True,
            "game": g,
            "players": player_views,
            "can_add_sims": can_add_sims,
            "added": added,
        },
    )


async def _render_add_sims(
    request: Request,
    db,
    user: User,
    game: Match,
    *,
    error: str | None = None,
    prefill: list[tuple[str, str]] | None = None,
    status_code: int = 200,
):
    """Render the Add Sims screen with the catalog and live-roster data."""
    existing = list(
        (
            await db.execute(
                select(Player.seat_name).where(
                    Player.match_id == game.id, Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    can_add = game.state in (GameState.SCHEDULED, GameState.REGISTERING)
    sims_data = {
        "maxPlayers": game.max_players,
        "currentCount": len(existing),
        "existing": existing,
        "names": list(SIM_NAME_POOL),
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
        "admin/add_sims.html",
        {
            "user": user,
            "is_admin": True,
            "game": game,
            "personalities": PERSONALITIES,
            "packs": PACKS,
            "can_add": can_add,
            "current_count": len(existing),
            "error": error,
            "sims_data": sims_data,
        },
        status_code=status_code,
    )


@router.get("/admin/matches/{match_id}/sims", response_class=HTMLResponse)
@router.get("/admin/games/{match_id}/sims", response_class=HTMLResponse)
async def add_sims_form(
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    g = await _load_match_or_404(db, match_id)
    return await _render_add_sims(request, db, user, g)


@router.post("/admin/matches/{match_id}/sims")
@router.post("/admin/games/{match_id}/sims")
async def add_sims_submit(
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    seat_name: Annotated[list[str] | None, Form()] = None,
    seat_strategy: Annotated[list[str] | None, Form()] = None,
):
    g = await _load_match_or_404(db, match_id)
    if g.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        return await _render_add_sims(
            request,
            db,
            user,
            g,
            error="Sims can only be added before a game starts.",
            status_code=409,
        )
    names = [n.strip() for n in (seat_name or [])]
    strategies = [s.strip() for s in (seat_strategy or [])]
    if len(names) != len(strategies):
        return await _render_add_sims(
            request,
            db,
            user,
            g,
            error="Something went wrong reading the roster. Please try again.",
            status_code=400,
        )
    seats = list(zip(names, strategies))
    try:
        created = await add_sims_to_game(db, g, seats)
    except SimSeatingError as exc:
        return await _render_add_sims(
            request, db, user, g, error=str(exc), prefill=seats, status_code=400
        )
    return RedirectResponse(
        url=f"/admin/matches/{match_id}?added={len(created)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/matches/{match_id}/start")
@router.post("/admin/games/{match_id}/start")
async def admin_start_game(
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
):
    """Force a REGISTERING game to start now (manual override of the auto-start poller)."""
    g = await _load_match_or_404(db, match_id)
    try:
        await start_game(db, g)
    except TransitionError:
        raise HTTPException(409, detail=f"Cannot start a game in state {g.state.value}.")
    return RedirectResponse(
        url=f"/admin/matches/{match_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/matches/{match_id}/delete")
@router.post("/admin/games/{match_id}/delete")
async def admin_delete_game(
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_admin)],
    next: Annotated[str, Form()] = "/admin",
):
    """Permanently delete a game and everything under it. Admin only.

    Deletes in FK-safe order: clear the game's winner pointer, then
    submissions → turns → players → the game itself. Stops the game's loop
    first if it happens to be running.
    """
    g = await _load_match_or_404(db, match_id)

    registry.stop(match_id)  # no-op if not running

    # Break the games → players FK so players can be deleted.
    g.winner_player_id = None
    await db.flush()

    turn_ids = (
        (await db.execute(select(Turn.id).where(Turn.match_id == match_id))).scalars().all()
    )
    if turn_ids:
        await db.execute(delete(TurnSubmission).where(TurnSubmission.turn_id.in_(turn_ids)))
    await db.execute(delete(Turn).where(Turn.match_id == match_id))

    await db.execute(delete(Player).where(Player.match_id == match_id))

    await db.execute(delete(Match).where(Match.id == match_id))
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
        (
            await db.execute(
                select(Player.match_id, Player.seat_name, AgentVersion)
                .join(AgentVersion, AgentVersion.id == Player.agent_version_id)
                .order_by(AgentVersion.created_at.desc())
            )
        ).all()
    )
    rows = [
        {
            "match_id": match_id,
            "agent_id": seat_name,
            "created_at": version.created_at.isoformat(),
            "is_default": version.version_no == 1,
            "prompt": version.strategy_text,
        }
        for match_id, seat_name, version in prompts
    ]
    return templates.TemplateResponse(
        request,
        "admin/prompts.html",
        {"user": user, "is_admin": True, "rows": rows},
    )
