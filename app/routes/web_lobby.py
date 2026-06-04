"""Marketing, game catalog, play hub, and lobby web routes."""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.deps import DbSession, get_current_user
from app.engine.bot_activity import compute_bot_health
from app.engine.scheduler import cancel_overdue_unfilled_games
from app.games import get as get_game_module
from app.games.base import GameError
from app.models.bot import Bot, BotKind
from app.models.match import Match, GameState, MatchKind
from app.models.player import Player
from app.routes.web_support import (
    _TEST_NAME_PREFIX,
    _is_admin,
    _is_showcase,
    _player_count,
    _redirect_to_match,
    _top_standings,
    _upcoming_views,
)
from app.routes.web_viewer import _build_rc_data, _game_view_context
from app.templating import templates

router = APIRouter(tags=["web"])
logger = logging.getLogger(__name__)

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
        ctx = await _game_view_context(request, db, match_id)
        return match_id, _build_rc_data(ctx["scoreboard"], ctx["history"])
    except Exception:
        logger.exception("Failed to build robot-circle replay data for %s", match_id)
        return match_id, ""


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

    # Leaderboard band: real standings. Prefer the most-progressed live game;
    # otherwise the most-recent finished showcase game. Empty list → empty state.
    live.sort(key=lambda v: (v["current_round"], v["current_turn"]), reverse=True)
    standings: list[dict] = []
    standings_game: str | None = None
    standings_source = next((v for v in live), None) or next(
        (v for v in completed if _is_showcase(v)), None
    )
    if standings_source is not None:
        standings = await _top_standings(db, standings_source["id"], 6)
        standings_game = standings_source["name"]

    return templates.TemplateResponse(
        request,
        "agent_ludum.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "rc_data": rc_data,
            "rc_game_id": rc_game_id,
            "rc_game_type": rc_game_type,
            "standings": standings,
            "standings_game": standings_game,
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


@router.get("/play", response_class=HTMLResponse)
async def operator_join_page(request: Request, db: DbSession):
    """Operator-facing join hub: bot status, Practice Arena, next auto-match."""
    user = await get_current_user(request, db)

    practice_arena: dict | None = None
    next_auto_match: dict | None = None
    bot_rows: list[dict] = []
    my_entries: list[dict] = []

    # Upcoming arena/auto-match cards — visible to all visitors.
    pa_match = (
        await db.execute(
            select(Match).where(
                Match.match_kind == MatchKind.PRACTICE_ARENA.value,
                Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
            )
        )
    ).scalars().first()
    if pa_match is not None:
        practice_arena = {
            "id": pa_match.id,
            "name": pa_match.name,
            "game": pa_match.game,
            "player_count": await _player_count(db, pa_match.id),
            "max_players": pa_match.max_players,
            "state": pa_match.state,
        }

    am_match = (
        await db.execute(
            select(Match).where(
                Match.match_kind == MatchKind.AUTO_SCHEDULED.value,
                Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                Match.scheduled_start >= datetime.now(timezone.utc),
            ).order_by(Match.scheduled_start)
        )
    ).scalars().first()
    if am_match is not None:
        next_auto_match = {
            "id": am_match.id,
            "name": am_match.name,
            "game": am_match.game,
            "scheduled_start": am_match.scheduled_start,
            "player_count": await _player_count(db, am_match.id),
            "max_players": am_match.max_players,
            "state": am_match.state,
        }

    if user is not None:
        bots = (
            await db.execute(
                select(Bot)
                .where(Bot.user_id == user.id, Bot.archived_at.is_(None), Bot.kind != BotKind.SIM)
                .order_by(Bot.name)
            )
        ).scalars().all()
        for bot in bots:
            health = await compute_bot_health(db, bot)
            bot_rows.append({"bot": bot, "health": health})

        active_players = (
            await db.execute(
                select(Player, Match)
                .join(Match, Player.match_id == Match.id)
                .where(
                    Player.user_id == user.id,
                    Player.left_at.is_(None),
                    Match.state.in_([GameState.ACTIVE, GameState.SCHEDULED, GameState.REGISTERING]),
                )
                .order_by(Match.scheduled_start)
            )
        ).all()
        my_entries = [
            {"player": p, "match": m}
            for p, m in active_players
        ]

    return templates.TemplateResponse(
        request,
        "play.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "practice_arena": practice_arena,
            "next_auto_match": next_auto_match,
            "bots": bot_rows,
            "my_entries": my_entries,
        },
    )


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
    recent = []
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
        elif g.state == GameState.COMPLETED:
            if g.winner_player_id:
                winner = (
                    await db.execute(select(Player).where(Player.id == g.winner_player_id))
                ).scalar_one_or_none()
                view["winner_agent_id"] = winner.agent_id if winner else None
            recent.append(view)
    upcoming = await _upcoming_views(db)

    # Marquee = the most-progressed live game (rounds, then turns).
    live.sort(key=lambda v: (v["current_round"], v["current_turn"]), reverse=True)
    # When nothing is live, replay the latest finished game with the same
    # robot-circle animation the platform front page uses.
    rc_game_id, rc_data = (None, "") if live else await _showcase_replay_data(request, db, recent)
    # Keep smoke-test games out of the public recent list.
    recent_display = [
        v for v in recent if not str(v["name"]).strip().lower().startswith(_TEST_NAME_PREFIX)
    ]

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "live_games": live,
            "upcoming_games": upcoming,
            "recent_games": recent_display[:8],
            "rc_game_id": rc_game_id,
            "rc_data": rc_data,
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
