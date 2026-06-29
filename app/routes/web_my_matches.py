"""'My games' dashboard, the player slot dashboard, and the leave action."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select

from app.config import settings
from app.deps import DbSession, require_user
from app.game_types import DEFAULT_GAME_TYPE
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User, UserRole
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _load_owned_player_match_or_404,
)
from app.templating import templates

router = APIRouter(tags=["web"])


@router.get("/me/matches", response_class=HTMLResponse)
async def my_matches(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    players = (
        (await db.execute(select(Player).where(Player.user_id == user.id))).scalars().all()
    )
    owned_matches = (
        (
            await db.execute(
                select(Match)
                .where(Match.created_by_user_id == user.id)
                .order_by(Match.scheduled_start.desc(), Match.id.desc())
            )
        )
        .scalars()
        .all()
    )
    match_ids = {p.match_id for p in players}
    match_ids.update(m.id for m in owned_matches)
    if not match_ids:
        return templates.TemplateResponse(
            request,
            "my_matches.html",
            {"user": user, "is_admin": _is_any_admin(user), "game_sections": []},
        )
    match_id_list = list(match_ids)

    matches = {
        m.id: m
        for m in (await db.execute(select(Match).where(Match.id.in_(match_id_list)))).scalars().all()
    }

    own_seats_by_match: dict[str, list[str]] = {}
    for p in players:
        own_seats_by_match.setdefault(p.match_id, []).append(p.seat_name)

    count_rows = (await db.execute(
        select(
            Player.match_id,
            func.count(Player.id).label("total"),
            func.sum(case((Agent.kind == AgentKind.BOT, 1), else_=0)).label("bot_count"),
        )
        .join(Agent, Agent.id == Player.agent_id)
        .where(Player.match_id.in_(match_id_list))
        .group_by(Player.match_id)
    )).all()
    counts_by_match = {row.match_id: row for row in count_rows}

    sections_map: dict[str, dict] = {}
    ordered_matches = sorted(
        matches.values(), key=lambda g: (g.scheduled_start, g.id), reverse=True
    )
    for g in ordered_matches:
        slug = g.game
        if slug not in sections_map:
            title = {DEFAULT_GAME_TYPE: "Hoard Hurt Help"}.get(slug, slug.replace("-", " ").title())
            sections_map[slug] = {"title": title, "active": [], "completed": [], "cancelled": []}

        row = counts_by_match.get(g.id)
        total = int(row.total or 0) if row else 0
        bot_count = int(row.bot_count or 0) if row else 0
        agent_count = total - bot_count
        parts: list[str] = []
        if agent_count:
            parts.append(f"{agent_count} {'agent' if agent_count == 1 else 'agents'}")
        if bot_count:
            parts.append(f"{bot_count} {'bot' if bot_count == 1 else 'bots'}")
        players_label = ", ".join(parts) if parts else "0 players"
        activity_bits: list[str] = []
        own_seats = sorted(own_seats_by_match.get(g.id, []))
        if own_seats:
            activity_bits.append(f"Playing as {', '.join(own_seats)}")
        if g.created_by_user_id == user.id:
            activity_bits.append("Created by you")

        entry = {
            "id": g.id,
            "name": g.name,
            "state": g.state,
            "watch_url": f"/games/{g.game}/matches/{g.id}",
            "activity_label": " · ".join(activity_bits),
            "players_label": players_label,
            "can_delete": user.role == UserRole.ADMIN
            or (g.created_by_user_id == user.id and g.state in (GameState.SCHEDULED, GameState.REGISTERING)),
            "delete_url": f"/matches/{g.id}/delete",
        }
        if g.state == GameState.COMPLETED:
            sections_map[slug]["completed"].append(entry)
        elif g.state == GameState.CANCELLED:
            sections_map[slug]["cancelled"].append(entry)
        else:
            sections_map[slug]["active"].append(entry)

    return templates.TemplateResponse(
        request,
        "my_matches.html",
        {"user": user, "is_admin": _is_any_admin(user), "game_sections": list(sections_map.values())},
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
        missing_detail="Agent slot not found.",
    )
    presets = [asdict(p) for p in get_game_module(game.game).strategy_presets()]

    agent_row = (
        await db.execute(
            select(Agent, AgentVersion)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(Agent.id == player.agent_id)
        )
    ).one_or_none()
    current_agent: Agent | None = None
    current_version: AgentVersion | None = None
    if agent_row is not None:
        current_agent, current_version = agent_row

    fresh_key: str | None = None

    selected_ai = request.session.pop(f"ai_type_{player.id}", None)
    pre_game = game.state in (GameState.SCHEDULED, GameState.REGISTERING)

    return templates.TemplateResponse(
        request,
        "connection.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game": game,
            "game_theme": _game_theme(game),
            "player": player,
            "agent": current_agent,
            "version": current_version,
            "agent_key": fresh_key,
            "strategy": current_version.strategy_text if current_version else "",
            "base_url": settings.base_url,
            "selected_ai": selected_ai,
            "presets": presets,
            "just_saved": saved,
            "can_edit_strategy": False,
            "can_leave": pre_game,
            "pre_game": pre_game,
        },
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
