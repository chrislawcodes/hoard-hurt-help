"""Admin HTML pages — platform-admin only."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from app.deps import DbSession, require_platform_admin
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.models.user import User
from app.routes.web_support import _seated_player_count
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    all_games = (
        (await db.execute(select(Match).order_by(Match.scheduled_start.desc()))).scalars().all()
    )
    active, scheduled, completed = [], [], []
    for g in all_games:
        view = {
            "id": g.id,
            "game": g.game,
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


@router.post("/admin/matches/{match_id}/delete")
async def admin_delete_match(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
):
    match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise HTTPException(404, detail=f"Match {match_id} not found.")
    turn_ids = select(Turn.id).where(Turn.match_id == match_id)
    await db.execute(delete(TurnSubmission).where(TurnSubmission.turn_id.in_(turn_ids)))
    await db.execute(delete(TurnMessage).where(TurnMessage.turn_id.in_(turn_ids)))
    await db.execute(delete(Turn).where(Turn.match_id == match_id))
    await db.execute(delete(Player).where(Player.match_id == match_id))
    await db.execute(delete(RequestIncident).where(RequestIncident.match_id == match_id))
    await db.execute(delete(Match).where(Match.id == match_id))
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/handles", response_class=HTMLResponse)
async def admin_handles(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
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
    user: Annotated[User, Depends(require_platform_admin)],
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
    user: Annotated[User, Depends(require_platform_admin)],
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
    user: Annotated[User, Depends(require_platform_admin)],
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
