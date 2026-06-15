"""Lobby + 'upcoming' fragment routes, and the aggregated human web router.

The human-facing web surface that used to live in this one file is now split
into focused sibling modules:

* ``web_front_page``      — the Agent Ludum marketing front page (``/``)
* ``web_games_catalog``   — the game catalog and play hub
* ``web_leaderboard``     — the global leaderboard
* ``web_legacy_redirects``— legacy ``/play/...`` -> ``/games/...`` redirects
* ``web_account_notice``  — the public ``/disabled`` account notice

This module keeps the lobby board (``/games/{game}``) and its polled
``/games/{game}/upcoming`` fragment. They stay here on purpose: tests
monkeypatch ``app.routes.web_lobby.cancel_overdue_unfilled_games``,
``app.routes.web_lobby._upcoming_views``, and (via ``_showcase_replay_data``)
``app.routes.web_lobby._game_view_context``. Those handlers resolve those names
from *this* module's namespace, so the handlers must be defined here for the
patches to take effect.

``router`` aggregates every sibling router plus the lobby routes, so
``app.routes.web`` still mounts just ``web_lobby.router`` and the full URL
surface is preserved unchanged. It also re-exports the moved public symbols so
other modules and tests can keep importing them from ``app.routes.web_lobby``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlencode
from typing import Any
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.deps import DbSession, get_current_user
from app.engine.connection_activity import compute_bot_health
from app.engine.scheduler import cancel_overdue_unfilled_games
from app.games import get as get_game_module
from app.games import is_admin_only
from app.games.base import GameError
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection
from app.models.match import Match, GameState
from app.models.player import Player
from app.ops_events import log_ops_event
from app.read_models.agent_display import agent_display_name
from app.read_models.matches import count_players_by_match
from app.routes import (
    web_account_notice,
    web_front_page,
    web_games_catalog,
    web_leaderboard,
    web_legacy_redirects,
)
from app.routes.web_support import (
    _TEST_NAME_PREFIX,
    _is_any_admin,
    _is_showcase,
    _load_match_or_404,
    _redirect_to_match,
    _batch_top_standings,
    _upcoming_views,
)
from app.routes.viewer_presentation import _build_rc_data, sample_replay_data
from app.routes.web_viewer import _game_view_context

# Re-export the moved public symbols so existing imports from this module keep
# working without change.
from app.routes.web_front_page import home
from app.routes.web_games_catalog import (
    _game_display_name,
    _game_tagline,
    agent_instructions_page,
    games_catalog,
    operator_join_page,
)
from app.routes.web_leaderboard import _leaderboard_url, leaderboard_page
from app.routes.web_legacy_redirects import (
    legacy_play_redirect,
    legacy_play_upcoming_redirect,
)
from app.routes.web_account_notice import account_disabled
from app.templating import templates

logger = logging.getLogger(__name__)


async def _showcase_replay_data(
    request: Request, db, completed_views: list[dict]
) -> tuple[str | None, str]:
    """Robot-circle replay of the most-recent completed showcase game.

    Returns ``(match_id, rc_data_json)``. When no finished showcase game exists
    (or building its data fails), ``match_id`` is None and the JSON is a bundled
    sample replay so the animation always plays instead of a dead placeholder.
    Shared by the platform front page and the Hoard·Hurt·Help lobby so both
    replay the same latest game the same way.
    """
    match_id = next((v["id"] for v in completed_views if _is_showcase(v)), None)
    if not match_id:
        return None, sample_replay_data()
    try:
        match = await _load_match_or_404(db, match_id)
        ctx = await _game_view_context(request, db, match)
        return match_id, _build_rc_data(ctx["scoreboard"], ctx["history"])
    except SQLAlchemyError:
        log_ops_event(
            logger,
            logging.ERROR,
            "replay_fallback",
            f"DB error building robot-circle replay for match {match_id};"
            " falling back to sample",
            match_id=match_id,
        )
        return None, sample_replay_data()


def _lobby_timestamp(match: Match) -> datetime:
    """Pick the timestamp we want to show for a finished or cancelled match."""

    return match.completed_at or match.cancelled_at or match.started_at or match.scheduled_start


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
    winner_rows: dict[int, dict[str, Any]] = {}
    if winner_ids:
        winner_rows = {
            player_id: {
                "display_name": agent_display_name(agent),
                "is_bot": agent.kind == AgentKind.BOT,
            }
            for player_id, agent in (
                await db.execute(
                    select(Player.id, Agent)
                    .join(Agent, Agent.id == Player.agent_id)
                    .where(Player.id.in_(winner_ids))
                )
            ).all()
        }

    completed: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    bots_only: list[dict[str, Any]] = []
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
            "winner_display_name": (
                winner_rows.get(match.winner_player_id, {}).get("display_name")
                if match.winner_player_id
                else None
            ),
            "winner_is_bot": (
                winner_rows.get(match.winner_player_id, {}).get("is_bot", False)
                if match.winner_player_id
                else False
            ),
            "watch_url": f"/games/{match.game}/matches/{match.id}",
        }
        if view["winner_display_name"]:
            view["summary"] = f"Won by {view['winner_display_name']}"
        elif match.state == GameState.COMPLETED:
            view["summary"] = "Finished"
        else:
            view["summary"] = "Cancelled"
        if match.state == GameState.COMPLETED:
            completed.append(view)
            if int(agent_count) > 0:
                recent.append(view)
            elif int(player_count) > 0:
                bots_only.append(view)
        elif match.state == GameState.CANCELLED:
            cancelled.append(view)

    for group in (completed, recent, bots_only, cancelled):
        group.sort(key=lambda v: v["timestamp"], reverse=True)

    return {
        "completed": completed,
        "recent": recent,
        "bots_only": bots_only,
        "cancelled": cancelled,
    }


# Lobby board + polled 'upcoming' fragment. Defined on a local router; spliced
# into the aggregated `router` below in the original registration order.
_lobby_router = APIRouter(tags=["web"])


@_lobby_router.get("/games/{game}", response_class=HTMLResponse)
async def game_lobby(request: Request, db: DbSession, game: Annotated[str, Path()]):
    """Lobby for a game title, or a legacy redirect for old match ids."""
    try:
        module = get_game_module(game)
    except GameError:
        return await _redirect_to_match(db, game)
    user = await get_current_user(request, db)
    # Hide an admin-only (under-construction) game from non-admins: 404 so its
    # existence isn't even revealed.
    if is_admin_only(game) and not _is_any_admin(user):
        raise HTTPException(status_code=404, detail="Game not found.")
    # Self-heal before reading: a game past its start time with too few players
    # should show as cancelled, not linger as "Upcoming" with a live Join button.
    # The background poller normally does this within seconds, but the lobby must
    # not depend on it having run. A DB failure here must never break the page —
    # log and fall through to whatever state the DB already holds.
    try:
        await cancel_overdue_unfilled_games(db)
    except SQLAlchemyError:
        log_ops_event(
            logger,
            logging.ERROR,
            "lobby_reconciliation_failed",
            "lobby: DB error during overdue-game reconciliation; rendering current state",
            route="game_lobby",
        )
    # Only live games feed the marquee. Upcoming is built separately via
    # _upcoming_views, and finished/cancelled via _lobby_recent_views — so this
    # query asks the DB for ACTIVE matches only. Loading every match ever played
    # just to filter for the live few was O(all matches): a full-table scan that
    # grew on every finished game. Active games carry their standings, and their
    # player counts come from one grouped query.
    active_games = (
        (
            await db.execute(
                select(Match)
                .where(Match.state == GameState.ACTIVE)
                .order_by(Match.scheduled_start.desc())
            )
        )
        .scalars()
        .all()
    )
    active_player_counts = await count_players_by_match(
        db, [g.id for g in active_games], active_only=True
    )
    # Fetch all standings in one batched query instead of N separate queries.
    all_standings = await _batch_top_standings(
        db, [g.id for g in active_games], limit=3
    )
    live = []
    for g in active_games:
        live.append(
            {
                "id": g.id,
                "game_type": g.game,
                "name": g.name,
                "scheduled_start": g.scheduled_start,
                "state": g.state,
                "min_players": g.min_players,
                "max_players": g.max_players,
                "current_round": g.current_round,
                "current_turn": g.current_turn,
                "winner_agent_id": None,
                # The marquee shows "who's leading", so a live game carries its top-3.
                "standings": all_standings.get(g.id, []),
                "player_count": active_player_counts.get(g.id, 0),
            }
        )
    upcoming = await _upcoming_views(db)
    finished_views = await _lobby_recent_views(db)
    show_recent_all = request.query_params.get("recent") == "all"
    show_bots_all = request.query_params.get("sims") == "all"
    show_cancelled_all = request.query_params.get("cancelled") == "all"

    # Marquee = the most-progressed live game (rounds, then turns).
    live.sort(key=lambda v: (v["current_round"], v["current_turn"]), reverse=True)
    # When nothing is live, replay the latest finished game with the same
    # robot-circle animation the platform front page uses.
    rc_game_id, rc_data = (None, "") if live else await _showcase_replay_data(
        request, db, finished_views["completed"]
    )
    recent_games = finished_views["recent"]
    bots_only_games = finished_views["bots_only"]

    # Onboarding banner: shown when the user has a warm agent but hasn't joined a
    # match yet. Disappears naturally once they're in a game.
    show_onboarding_banner = False
    if user is not None:
        # Agents are no longer attached to a connection: the user has a "warm
        # agent" when they own an AI agent and have a live/ready connection to
        # serve it. compute_bot_health already reflects provider coverage.
        owns_ai_agent = bool(
            await db.scalar(
                select(func.count())
                .select_from(Agent)
                .where(
                    Agent.user_id == user.id,
                    Agent.archived_at.is_(None),
                    Agent.kind == AgentKind.AI,
                )
            )
        )
        user_connections = (
            await db.execute(
                select(Connection).where(
                    Connection.user_id == user.id,
                    Connection.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        has_warm_agent = False
        if owns_ai_agent:
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
            "is_admin": _is_any_admin(user),
            "create_match_url": f"/games/{game}/matches/new",
            "live_games": live,
            "upcoming_games": upcoming,
            "recent_games": recent_games[:5] if not show_recent_all else recent_games,
            "recent_games_total": len(recent_games),
            "recent_games_toggle_url": _toggle_url("recent-games", "recent", show_recent_all)
            if len(recent_games) > 5
            else None,
            "recent_games_toggle_label": "Show fewer" if show_recent_all else "See all",
            "show_recent_all": show_recent_all,
            "bots_only_games": bots_only_games[:5] if not show_bots_all else bots_only_games,
            "bots_only_games_total": len(bots_only_games),
            "bots_only_games_toggle_url": _toggle_url("bots-only-games", "sims", show_bots_all)
            if len(bots_only_games) > 5
            else None,
            "bots_only_games_toggle_label": "Show fewer" if show_bots_all else "See all",
            "show_bots_all": show_bots_all,
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


@_lobby_router.get("/games/{game}/upcoming", response_class=HTMLResponse)
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
    if is_admin_only(game) and not _is_any_admin(user):
        raise HTTPException(404)
    try:
        await cancel_overdue_unfilled_games(db)
    except SQLAlchemyError:
        log_ops_event(
            logger,
            logging.ERROR,
            "lobby_reconciliation_failed",
            "lobby upcoming: DB error during overdue-game reconciliation; rendering current state",
            route="game_upcoming",
        )
    return templates.TemplateResponse(
        request,
        "fragments/lobby_upcoming.html",
        {
            "is_admin": _is_any_admin(user),
            "upcoming_games": await _upcoming_views(db),
            "game_theme": module.theme(),
        },
    )


# Aggregate every human web route onto a single router so app.routes.web can
# keep mounting just `web_lobby.router`. The front page is served at the empty
# path (""), which `include_router` rejects when the parent prefix is also empty
# ("Prefix and path cannot be both empty"). Splicing each sub-router's routes
# preserves every path, method, and dependency exactly while keeping the
# empty-path route. Order matches the original single-file registration order so
# route matching is identical (literal `/games` and `/play` registered before
# the `/games/{game}` and `/play/{game}` capture routes).
def _route_by_path(sub_router: APIRouter, path: str) -> Any:
    """Pick a single registered route from a sub-router by its exact path.

    Lets us splice routes in the original single-file order regardless of how
    they happen to sit inside each sub-router.
    """
    for route in sub_router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise RuntimeError(f"route {path!r} not found on sub-router")


router = APIRouter(tags=["web"])
# Original single-file registration order, preserved exactly so route matching
# is identical (literal `/games` and `/play` registered before the
# `/games/{game}` and `/play/{game}` capture routes).
router.routes.extend(web_front_page.router.routes)  # /
router.routes.append(_route_by_path(web_games_catalog.router, "/games"))
router.routes.extend(web_leaderboard.router.routes)  # /leaderboard
router.routes.append(_route_by_path(web_games_catalog.router, "/play"))
router.routes.append(_route_by_path(web_legacy_redirects.router, "/play/{game}"))
router.routes.append(
    _route_by_path(web_games_catalog.router, "/games/{game}/agent-instructions")
)
router.routes.extend(_lobby_router.routes)  # /games/{game}, /games/{game}/upcoming
router.routes.append(_route_by_path(web_legacy_redirects.router, "/play/{game}/upcoming"))
router.routes.extend(web_account_notice.router.routes)  # /disabled


__all__ = [
    "router",
    # Lobby board + fragment (defined here).
    "game_lobby",
    "game_upcoming",
    "_showcase_replay_data",
    "_lobby_timestamp",
    "_lobby_recent_views",
    # Names tests monkeypatch on this module; kept importable from here.
    "cancel_overdue_unfilled_games",
    "_upcoming_views",
    "_game_view_context",
    # Re-exported public symbols from the split sibling modules.
    "home",
    "games_catalog",
    "operator_join_page",
    "agent_instructions_page",
    "_game_display_name",
    "_game_tagline",
    "leaderboard_page",
    "_leaderboard_url",
    "legacy_play_redirect",
    "legacy_play_upcoming_redirect",
    "account_disabled",
]
