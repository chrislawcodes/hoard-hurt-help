"""Per-game match management pages.

Who reaches what:

* the dashboard and the strategy prompts page are **platform admin** — both
  list every match (or every prompt) in a game, which is an organizer's view;
* the match detail page is **owner or platform admin**, with other players'
  strategy text hidden from a non-admin;
* start and cancel are **platform admin**. Cancel is an organizer's power (a
  player deletes their own pre-start match instead), and this start path calls
  ``start_game`` directly, skipping the seat and player-floor checks the player
  start route runs through ``viewer_start_eligibility``.

Match *creation* is not here. Players and admins both create through
``matches_user.py``; this module's create form was merged into it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.deps import DbSession, require_user
from app.engine.bots.roster import personality_display_name
from app.engine.match_deletion import cancel_blocked_reason, cancel_match
from app.engine.scheduler import start_game
from app.engine.state_machine import TransitionError
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User, UserRole
from app.routes.match_authz import (
    AdminMatch,
    OwnedOrAdminMatch,
    PlatformAdminForGame,
)
from app.routes.web_support import _bucket_matches
from app.templating import templates

router = APIRouter(prefix="/games/{game}/admin", tags=["match-manage"])


@router.get("/", response_class=HTMLResponse)
async def match_dashboard(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: PlatformAdminForGame,
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

    def _view(m: Match, player_count: int) -> dict:
        return {
            "id": m.id,
            "name": m.name,
            "scheduled_start": m.scheduled_start,
            "current_round": m.current_round,
            "total_rounds": m.total_rounds,
            "state": m.state,
            "player_count": player_count,
        }

    active, scheduled, completed = await _bucket_matches(db, all_matches, _view)
    return templates.TemplateResponse(
        request,
        "match_manage/dashboard.html",
        {
            "user": user,
            "is_admin": True,
            "game_slug": game,
            "active_games": active,
            "scheduled_games": scheduled,
            "completed_games": completed,
        },
    )


@router.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    g: OwnedOrAdminMatch,
    user: Annotated[User, Depends(require_user)],
    added: int | None = None,
):
    is_platform_admin = user.role == UserRole.ADMIN
    players = (
        (await db.execute(select(Player).where(Player.match_id == g.id))).scalars().all()
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
            personality_display_name(agent.bot_strategy or "")
            if is_bot and agent is not None
            else ""
        )
        # A bot's strategy is a public preset, not a player's private prompt —
        # the owner who seated it must be able to see what they picked, and the
        # Type column already names it. Only a real player's AgentVersion text
        # is private, and only its owner (or an admin) may read it.
        if is_bot:
            strategy = agent.bot_strategy if agent else ""
        elif version is not None and (is_platform_admin or p.user_id == user.id):
            strategy = version.strategy_text
        else:
            strategy = ""
        player_views.append(
            {
                "agent_id": p.seat_name,
                "total_round_wins": p.total_round_wins,
                "total_round_score": p.total_round_score,
                "strategy": strategy,
                "is_bot": is_bot,
                "personality": personality,
            }
        )
    can_add_bots = g.state in (GameState.SCHEDULED, GameState.REGISTERING)
    return templates.TemplateResponse(
        request,
        "match_manage/match_detail.html",
        {
            "user": user,
            "is_admin": is_platform_admin,
            "game_slug": game,
            "match": g,
            "players": player_views,
            "can_add_bots": can_add_bots,
            "added": added,
        },
    )


@router.post("/matches/{match_id}/start")
async def start_match(
    game: Annotated[str, Path()],
    db: DbSession,
    g: AdminMatch,
):
    try:
        await start_game(db, g)
    except TransitionError:
        raise HTTPException(409, detail=f"Cannot start a match in state {g.state.value}.")
    return RedirectResponse(
        url=f"/games/{game}/admin/matches/{g.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/matches/{match_id}/cancel")
async def cancel_match_page(
    game: Annotated[str, Path()],
    db: DbSession,
    g: AdminMatch,
):
    reason = cancel_blocked_reason(g)
    if reason is not None:
        raise HTTPException(409, detail=reason)
    await cancel_match(db, g)
    return RedirectResponse(
        url=f"/games/{game}/admin",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/prompts", response_class=HTMLResponse)
async def strategy_prompts(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: PlatformAdminForGame,
):
    prompts = (
        await db.execute(
            select(Player.match_id, Player.seat_name, AgentVersion)
            .join(Match, Match.id == Player.match_id)
            .join(AgentVersion, AgentVersion.id == Player.agent_version_id)
            .where(Match.game == game)
            .order_by(AgentVersion.created_at.desc())
        )
    ).all()
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
        "match_manage/prompts.html",
        {"user": user, "is_admin": True, "game_slug": game, "rows": rows},
    )
