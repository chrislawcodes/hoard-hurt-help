"""Admin HTML pages — platform-admin only."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from app.config import settings
from app.deps import DbSession, require_platform_admin
from app.engine.match_deletion import delete_match
from app.games import known_types
from app.models.admin_audit_log import AdminAuditLog
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.user import User
from app.read_models.admin_reports import load_turn_timing_report
from app.routes.web_support import _seated_player_count
from app.services.admin_user_actions import (
    demote_user,
    disable_user,
    enable_user,
    promote_user,
    reset_handle,
)
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["admin"])
_USERS_PAGE_SIZE = 50


@router.get("/admin/matches", response_class=HTMLResponse)
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
            "match_kind": g.match_kind,
            "scheduled_start": g.scheduled_start,
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
            "game_types": known_types(),
        },
    )


@router.get("/admin/reports", response_class=HTMLResponse)
async def admin_reports(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    report = await load_turn_timing_report(db)
    return templates.TemplateResponse(
        request,
        "admin/reports.html",
        {"user": user, "is_admin": True, "report": report},
    )


@router.post("/admin/matches/{match_id}/delete")
async def admin_delete_match(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
):
    if (
        await db.execute(select(Match).where(Match.id == match_id))
    ).scalar_one_or_none() is None:
        raise HTTPException(404, detail=f"Match {match_id} not found.")
    await delete_match(db, match_id)
    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
    q: str | None = None,
    page: int = 1,
):
    page = max(1, page)
    stmt = select(User).order_by(User.created_at.desc())
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.handle).like(pattern),
            )
        )
    offset = (page - 1) * _USERS_PAGE_SIZE
    total = (await db.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (await db.execute(stmt.offset(offset).limit(_USERS_PAGE_SIZE))).scalars().all()

    user_ids = [u.id for u in rows]
    agent_counts: dict[int, int] = {}
    if user_ids:
        count_rows = (
            await db.execute(
                select(Agent.user_id, func.count().label("cnt"))
                .where(
                    Agent.user_id.in_(user_ids),
                    Agent.archived_at.is_(None),
                    Agent.kind == AgentKind.AI,
                )
                .group_by(Agent.user_id)
            )
        ).all()
        agent_counts = {uid: cnt for uid, cnt in count_rows}

    return templates.TemplateResponse(
        request,
        "admin/users_list.html",
        {
            "user": user,
            "is_admin": True,
            "rows": rows,
            "agent_counts": agent_counts,
            "q": q or "",
            "page": page,
            "total": total,
            "page_size": _USERS_PAGE_SIZE,
        },
    )


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
    user_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, detail="User not found.")

    connections = (
        await db.execute(
            select(Connection)
            .where(Connection.user_id == user_id, Connection.deleted_at.is_(None))
            .order_by(Connection.created_at.desc())
        )
    ).scalars().all()

    agents = (
        await db.execute(
            select(Agent)
            .where(
                Agent.user_id == user_id,
                Agent.archived_at.is_(None),
                Agent.kind == AgentKind.AI,
            )
            .order_by(Agent.created_at.desc())
        )
    ).scalars().all()

    recent_matches = (
        await db.execute(
            select(Match)
            .join(Player, Player.match_id == Match.id)
            .where(Player.user_id == user_id)
            .order_by(Match.scheduled_start.desc())
            .limit(20)
            .distinct()
        )
    ).scalars().all()

    audit_log = (
        await db.execute(
            select(AdminAuditLog, User)
            .join(User, User.id == AdminAuditLog.actor_user_id)
            .where(AdminAuditLog.target_user_id == user_id)
            .order_by(AdminAuditLog.created_at.desc())
            .limit(50)
        )
    ).all()
    audit_entries = [{"log": log, "actor": actor} for log, actor in audit_log]

    return templates.TemplateResponse(
        request,
        "admin/user_detail.html",
        {
            "user": user,
            "is_admin": True,
            "target": target,
            "connections": connections,
            "agents": agents,
            "recent_matches": recent_matches,
            "audit_entries": audit_entries,
            "floor_admin": target.email.lower() in settings.platform_admin_emails_set,
        },
    )


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
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    """Clear a user's handle. The string is freed immediately; the user picks a
    new one the next time they need it. Identity is keyed on users.id, so all
    leaderboard history is preserved."""
    await reset_handle(db, actor=user, target_id=user_id)
    await db.commit()
    return RedirectResponse(url="/admin/handles", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/users/{user_id}/disable")
async def admin_disable_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await disable_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/users/{user_id}/enable")
async def admin_enable_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await enable_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/users/{user_id}/promote")
async def admin_promote_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await promote_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/users/{user_id}/demote")
async def admin_demote_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await demote_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


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
