"""Marketing, game catalog, play hub, and lobby web routes."""

import dataclasses
import logging
from datetime import datetime
from urllib.parse import urlencode
from typing import Any
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select

from app.deps import DbSession, get_current_user
from app.engine.connection_activity import compute_bot_health
from app.engine.scheduler import cancel_overdue_unfilled_games
from app.games import get as get_game_module
from app.games.base import GameError
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection
from app.models.match import Match, GameState
from app.models.player import Player
from app.read_models.leaderboard import load_leaderboard_sections
from app.routes.web_support import (
    _TEST_NAME_PREFIX,
    _is_admin,
    _is_showcase,
    _load_match_or_404,
    _agent_count,
    _player_count,
    _redirect_to_match,
    _top_standings,
    _upcoming_views,
)
from app.routes.viewer_presentation import _build_rc_data
from app.routes.web_viewer import _game_view_context
from app.templating import templates

router = APIRouter(tags=["web"])
logger = logging.getLogger(__name__)


def _game_display_name(game_type: str) -> str:
    if game_type == "hoard-hurt-help":
        return "Hoard · Hurt · Help"
    return game_type.replace("-", " ").title()

async def _showcase_replay_data(
    request: Request, db, completed_views: list[dict]
) -> tuple[str | None, str]:
    """Robot-circle replay of the most-recent completed showcase game.

    Returns ``(match_id, rc_data_json)``. ``match_id`` is None and the JSON is ""
    when no finished showcase game exists. Shared by the platform front page and
    the Hoard·Hurt·Help lobby so both replay the same latest game the same way.
    """
    match_id = next((v["id"] for v in completed_views if _is_showcase(v)), None)
    if not match_id:
        return None, ""
    try:
        match = await _load_match_or_404(db, match_id)
        ctx = await _game_view_context(request, db, match)
        return match_id, _build_rc_data(ctx["scoreboard"], ctx["history"])
    except Exception:
        logger.exception("Failed to build robot-circle replay data for %s", match_id)
        return match_id, ""


def _lobby_timestamp(match: Match) -> datetime:
    """Pick the timestamp we want to show for a finished or cancelled match."""

    return match.completed_at or match.cancelled_at or match.started_at or match.scheduled_start


def _leaderboard_url(
    request: Request,
    *,
    rating: str | None = None,
    included: str | None = None,
    hide_sim_games: bool | None = None,
) -> str:
    """Build a leaderboard link while preserving the other active filters."""

    params = dict(request.query_params)
    if rating is not None:
        params["rating"] = rating
    if included is not None:
        params["included"] = included
    if hide_sim_games is not None:
        if hide_sim_games:
            params["hide_sim_games"] = "1"
        else:
            params.pop("hide_sim_games", None)
    return f"/leaderboard?{urlencode(params)}" if params else "/leaderboard"


async def _lobby_recent_views(db: DbSession) -> dict[str, list[dict[str, Any]]]:
    """Build the lobby's finished-match sections in one read-side projection."""

    player_counts = (
        select(
            Player.match_id.label("match_id"),
            func.count(Player.id).label("player_count"),
            func.coalesce(
                func.sum(case((Agent.kind == AgentKind.BOT, 1), else_=0)),
                0,
            ).label("bot_count"),
            func.coalesce(
                func.sum(case((Agent.kind != AgentKind.BOT, 1), else_=0)),
                0,
            ).label("agent_count"),
        )
        .join(Agent, Agent.id == Player.agent_id)
        .group_by(Player.match_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Match,
                func.coalesce(player_counts.c.player_count, 0),
                func.coalesce(player_counts.c.bot_count, 0),
                func.coalesce(player_counts.c.agent_count, 0),
            )
            .outerjoin(player_counts, player_counts.c.match_id == Match.id)
            .where(Match.state.in_([GameState.COMPLETED, GameState.CANCELLED]))
            .order_by(Match.scheduled_start.desc())
        )
    ).all()

    winner_ids = {
        match.winner_player_id
        for match, *_ in rows
        if match.state == GameState.COMPLETED and match.winner_player_id is not None
    }
    winner_names: dict[int, str] = {}
    if winner_ids:
        winner_names = {
            player_id: agent_id
            for player_id, agent_id in (
                await db.execute(select(Player.id, Player.seat_name).where(Player.id.in_(winner_ids)))
            ).all()
        }

    completed: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    sims_only: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    for match, player_count, bot_count, agent_count in rows:
        if str(match.name).strip().lower().startswith(_TEST_NAME_PREFIX):
            continue
        timestamp = _lobby_timestamp(match)
        view: dict[str, Any] = {
            "id": match.id,
            "game_type": match.game,
            "name": match.name,
            "state": match.state,
            "player_count": int(player_count),
            "bot_count": int(bot_count),
            "agent_count": int(agent_count),
            "timestamp": timestamp,
            "timestamp_label": "Completed" if match.state == GameState.COMPLETED else "Cancelled",
            "winner_agent_id": winner_names.get(match.winner_player_id) if match.winner_player_id else None,
            "watch_url": f"/games/{match.game}/matches/{match.id}",
        }
        if view["winner_agent_id"]:
            view["summary"] = f"Won by {view['winner_agent_id']}"
        elif match.state == GameState.COMPLETED:
            view["summary"] = "Finished"
        else:
            view["summary"] = "Cancelled"
        if match.state == GameState.COMPLETED:
            completed.append(view)
            if int(agent_count) > 0:
                recent.append(view)
            elif int(player_count) > 0:
                sims_only.append(view)
        elif match.state == GameState.CANCELLED:
            cancelled.append(view)

    for group in (completed, recent, sims_only, cancelled):
        group.sort(key=lambda v: v["timestamp"], reverse=True)

    return {
        "completed": completed,
        "recent": recent,
        "sims_only": sims_only,
        "cancelled": cancelled,
    }


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: DbSession):
    """Agent Ludum platform front page (marketing).

    Static explainer + funnel, plus two real-data regions: the hero match card
    (a real finished game's final-round replay) and the leaderboard band (real
    standings from the most-progressed live game, else the most-recent finished
    showcase game). Both fall back to honest empty states. The Hoard·Hurt·Help
    lobby itself lives one level down at `/games/hoard-hurt-help`.
    """
    user = await get_current_user(request, db)
    all_games = (
        (await db.execute(select(Match).order_by(Match.scheduled_start.desc()))).scalars().all()
    )
    live: list[dict] = []
    completed: list[dict] = []
    for g in all_games:
        view = {
            "id": g.id,
            "game_type": g.game,
            "name": g.name,
            "state": g.state,
            "current_round": g.current_round,
            "current_turn": g.current_turn,
            "winner_agent_id": None,
            "player_count": await _player_count(db, g.id),
        }
        if g.state == GameState.ACTIVE:
            live.append(view)
        elif g.state == GameState.COMPLETED:
            view["agent_count"] = await _agent_count(db, g.id)
            if g.winner_player_id:
                winner = (
                    await db.execute(select(Player).where(Player.id == g.winner_player_id))
                ).scalar_one_or_none()
                view["winner_agent_id"] = winner.agent_id if winner else None
            completed.append(view)

    # Robot-circle animation: most-recent completed showcase game — consistent
    # across page loads so the viewer always sees the same game.
    rc_game_id, rc_data = await _showcase_replay_data(request, db, completed)
    rc_game_type = next((v["game_type"] for v in completed if v["id"] == rc_game_id), None)

    # Leaderboard band: real ELO standings across all competitors (agents + sims),
    # sliced to the top 8 per game section for the home page teaser.
    lb_sections_full = await load_leaderboard_sections(db, included="all")
    lb_sections = [dataclasses.replace(s, rows=s.rows[:8]) for s in lb_sections_full]

    return templates.TemplateResponse(
        request,
        "agent_ludum.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "rc_data": rc_data,
            "rc_game_id": rc_game_id,
            "rc_game_type": rc_game_type,
            "lb_sections": lb_sections,
            "has_live": bool(live),
        },
    )


@router.get("/games", response_class=HTMLResponse)
async def games_catalog(request: Request, db: DbSession):
    """Catalog of the platform's playable game titles."""
    user = await get_current_user(request, db)
    module = get_game_module("hoard-hurt-help")
    return templates.TemplateResponse(
        request,
        "games.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game_theme": module.theme(),
            "featured_game_slug": "hoard-hurt-help",
        },
    )


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(
    request: Request,
    db: DbSession,
    rating: str = "standard",
    included: str = "agents",
    hide_sim_games: bool = False,
):
    """Global leaderboard, grouped by game."""
    user = await get_current_user(request, db)
    rating_mode = "bonus" if rating == "bonus" else "standard"
    included_mode = "sims" if included == "sims" else "all" if included == "all" else "agents"
    sections = await load_leaderboard_sections(
        db,
        rating_mode=rating_mode,
        included=included_mode,
    )
    if hide_sim_games:
        sections = [section for section in sections if not section.has_sims]
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "sections": sections,
            "rating_mode": rating_mode,
            "included": included_mode,
            "hide_sim_games": hide_sim_games,
            "rating_standard_url": _leaderboard_url(
                request, rating="standard", included=included_mode, hide_sim_games=hide_sim_games
            ),
            "rating_bonus_url": _leaderboard_url(
                request, rating="bonus", included=included_mode, hide_sim_games=hide_sim_games
            ),
            "included_agents_url": _leaderboard_url(
                request, rating=rating_mode, included="agents", hide_sim_games=hide_sim_games
            ),
            "included_sims_url": _leaderboard_url(
                request, rating=rating_mode, included="sims", hide_sim_games=hide_sim_games
            ),
            "included_all_url": _leaderboard_url(
                request, rating=rating_mode, included="all", hide_sim_games=hide_sim_games
            ),
            "sim_games_show_url": _leaderboard_url(
                request, rating=rating_mode, included=included_mode, hide_sim_games=False
            ),
            "sim_games_hide_url": _leaderboard_url(
                request, rating=rating_mode, included=included_mode, hide_sim_games=True
            ),
        },
    )


@router.get("/play")
async def operator_join_page(request: Request, db: DbSession):
    """Smart redirect: sends each visitor to the right next step.

    Not signed in → sign in (returning to agent setup).
    No handle → pick a handle first (agent setup requires one).
    No connected agent → the agents panel (create one, or connect it).
    Connected agent → lobby where they can join a match.
    """
    user = await get_current_user(request, db)

    if user is None:
        return RedirectResponse(
            "/auth/google/login?next=/games/hoard-hurt-help", status_code=status.HTTP_302_FOUND
        )

    if not user.handle:
        return RedirectResponse(
            "/me/handle?next=/games/hoard-hurt-help", status_code=status.HTTP_302_FOUND
        )

    return RedirectResponse("/games/hoard-hurt-help", status_code=status.HTTP_302_FOUND)


@router.get("/play/{game}", response_class=HTMLResponse)
async def legacy_play_redirect(game: Annotated[str, Path()]):
    return RedirectResponse(url=f"/games/{game}", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/games/{game}", response_class=HTMLResponse)
async def game_lobby(request: Request, db: DbSession, game: Annotated[str, Path()]):
    """Lobby for a game title, or a legacy redirect for old match ids."""
    try:
        module = get_game_module(game)
    except GameError:
        return await _redirect_to_match(db, game)
    user = await get_current_user(request, db)
    # Self-heal before reading: a game past its start time with too few players
    # should show as cancelled, not linger as "Upcoming" with a live Join button.
    # The background poller normally does this within seconds, but the lobby must
    # not depend on it having run. A failure here must never break the page — log
    # and fall through to whatever state the DB already holds.
    try:
        await cancel_overdue_unfilled_games(db)
    except Exception:
        logger.exception("lobby: failed to reconcile overdue games")
    all_games = (
        (await db.execute(select(Match).order_by(Match.scheduled_start.desc()))).scalars().all()
    )
    live = []
    for g in all_games:
        # Upcoming is built separately via _upcoming_views (shared with the polled
        # /upcoming fragment), so skip those states here.
        if g.state in (GameState.SCHEDULED, GameState.REGISTERING):
            continue
        view = {
            "id": g.id,
            "game_type": g.game,
            "name": g.name,
            "scheduled_start": g.scheduled_start.isoformat(),
            "state": g.state,
            "min_players": g.min_players,
            "max_players": g.max_players,
            "current_round": g.current_round,
            "current_turn": g.current_turn,
            "winner_agent_id": None,
            "player_count": await _player_count(db, g.id),
        }
        if g.state == GameState.ACTIVE:
            # The marquee shows "who's leading", so a live game carries its top-3.
            view["standings"] = await _top_standings(db, g.id, 3)
            live.append(view)
    upcoming = await _upcoming_views(db)
    finished_views = await _lobby_recent_views(db)
    show_recent_all = request.query_params.get("recent") == "all"
    show_sims_all = request.query_params.get("sims") == "all"
    show_cancelled_all = request.query_params.get("cancelled") == "all"

    # Marquee = the most-progressed live game (rounds, then turns).
    live.sort(key=lambda v: (v["current_round"], v["current_turn"]), reverse=True)
    # When nothing is live, replay the latest finished game with the same
    # robot-circle animation the platform front page uses.
    rc_game_id, rc_data = (None, "") if live else await _showcase_replay_data(
        request, db, finished_views["completed"]
    )
    recent_games = finished_views["recent"]
    sims_only_games = finished_views["sims_only"]

    # Onboarding banner: shown when the user has a warm agent but hasn't joined a
    # match yet. Disappears naturally once they're in a game.
    show_onboarding_banner = False
    if user is not None:
        user_connections = (
            await db.execute(
                select(Connection).distinct()
                .join(Agent, Agent.connection_id == Connection.id)
                .where(
                    Agent.user_id == user.id,
                    Agent.archived_at.is_(None),
                    Agent.kind == AgentKind.AI,
                )
            )
        ).scalars().all()
        has_warm_agent = False
        for connection in user_connections:
            health = await compute_bot_health(db, connection)
            if health.state.value in ("live", "ready"):
                has_warm_agent = True
                break
        if has_warm_agent:
            active_entry_count = await db.scalar(
                select(func.count()).select_from(Player)
                .join(Match, Player.match_id == Match.id)
                .where(
                    Player.user_id == user.id,
                    Player.left_at.is_(None),
                    Match.state.in_([GameState.ACTIVE, GameState.SCHEDULED, GameState.REGISTERING]),
                )
            ) or 0
            show_onboarding_banner = active_entry_count == 0
    cancelled_games = finished_views["cancelled"]

    def _toggle_url(section: str, key: str, show_all: bool) -> str:
        base = f"/games/{game}"
        params = dict(request.query_params)
        if show_all:
            params.pop(key, None)
        else:
            params[key] = "all"
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        return f"{base}{query}#{section}"

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "live_games": live,
            "upcoming_games": upcoming,
            "recent_games": recent_games[:5] if not show_recent_all else recent_games,
            "recent_games_total": len(recent_games),
            "recent_games_toggle_url": _toggle_url("recent-games", "recent", show_recent_all)
            if len(recent_games) > 5
            else None,
            "recent_games_toggle_label": "Show fewer" if show_recent_all else "See all",
            "show_recent_all": show_recent_all,
            "sims_only_games": sims_only_games[:5] if not show_sims_all else sims_only_games,
            "sims_only_games_total": len(sims_only_games),
            "sims_only_games_toggle_url": _toggle_url("sims-only-games", "sims", show_sims_all)
            if len(sims_only_games) > 5
            else None,
            "sims_only_games_toggle_label": "Show fewer" if show_sims_all else "See all",
            "show_sims_all": show_sims_all,
            "cancelled_games": cancelled_games[:5] if not show_cancelled_all else cancelled_games,
            "cancelled_games_total": len(cancelled_games),
            "cancelled_games_toggle_url": _toggle_url("cancelled-games", "cancelled", show_cancelled_all)
            if len(cancelled_games) > 5
            else None,
            "cancelled_games_toggle_label": "Show fewer" if show_cancelled_all else "See all",
            "show_cancelled_all": show_cancelled_all,
            "rc_game_id": rc_game_id,
            "rc_data": rc_data,
            "show_onboarding_banner": show_onboarding_banner,
            # Tint the lobby's content with this game's scheme; the shared chrome
            # (defined outside <main>) is untouched. See GameModule.theme().
            "game_theme": module.theme(),
        },
    )


@router.get("/games/{game}/upcoming", response_class=HTMLResponse)
async def game_upcoming(request: Request, db: DbSession, game: Annotated[str, Path()]):
    """Polled fragment of the lobby's 'Upcoming' list, reconciled on each fetch.

    home.html refreshes this every 60s so an already-open lobby self-updates: a
    game that fills and starts drops off, and one that passes its start time
    under-filled is cancelled and drops off — no manual reload needed. A failure
    to reconcile must not break the fragment, so log and render current state.
    """
    user = await get_current_user(request, db)
    try:
        module = get_game_module(game)
    except GameError:
        raise HTTPException(404)
    try:
        await cancel_overdue_unfilled_games(db)
    except Exception:
        logger.exception("lobby upcoming: failed to reconcile overdue games")
    return templates.TemplateResponse(
        request,
        "fragments/lobby_upcoming.html",
        {
            "is_admin": _is_admin(user),
            "upcoming_games": await _upcoming_views(db),
            "game_theme": module.theme(),
        },
    )


@router.get("/play/{game}/upcoming", response_class=HTMLResponse)
async def legacy_play_upcoming_redirect(game: Annotated[str, Path()]):
    return RedirectResponse(
        url=f"/games/{game}/upcoming", status_code=status.HTTP_301_MOVED_PERMANENTLY
    )
