"""Spectator analysis web routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, get_current_user
from app.engine.game_insights import round_detail, season_overview
from app.engine.game_records import ActionRecord, PlayerRecord
from app.models.match import Match, GameState
from app.read_models.matches import load_action_records, load_player_records
from app.routes.web_support import (
    _game_theme,
    _is_admin,
    _load_match_or_404,
    _redirect_if_game_slug_mismatch,
    _redirect_to_match,
)
from app.templating import templates

router = APIRouter(tags=["web"])

async def _insight_records(db, game: Match) -> tuple[list[PlayerRecord], list[ActionRecord]]:
    """Map DB rows to the DB-free records the insights engine consumes."""
    return (
        await load_player_records(db, game.id, active_only=False),
        await load_action_records(db, game.id),
    )


@router.get("/games/{game}/matches/{match_id}/analysis", response_class=HTMLResponse)
async def game_analysis(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    """Season home for the spectator analysis — the round-win race, results,
    grudges, and (when live) a peek into the current round."""
    user = await get_current_user(request, db)
    g = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(g, game, "/analysis"):
        return redirect
    players, actions = await _insight_records(db, g)
    active = g.state == GameState.ACTIVE
    overview = season_overview(players, actions, g.total_rounds, g.current_round, active)
    zero_wins = sum(1 for s in overview.standings if s.round_wins == 0)
    rounds_played = set(overview.rounds_played)
    live_peek = (
        round_detail(g.current_round, players, actions)
        if active and g.current_round in rounds_played
        else None
    )
    return templates.TemplateResponse(
        request,
        "analysis_season.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": g,
            "game_theme": _game_theme(g),
            "overview": overview,
            "zero_wins": zero_wins,
            "live_peek": live_peek,
        },
    )


@router.get("/games/{match_id}/analysis", include_in_schema=False)
async def legacy_game_analysis_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/analysis")


@router.get(
    "/games/{game}/matches/{match_id}/analysis/rounds/{round_num}",
    response_class=HTMLResponse,
)
async def game_analysis_round(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    round_num: Annotated[int, Path()],
    request: Request,
    db: DbSession,
):
    """Drill-in for one round: leaderboard-from-0, mood, alliances, event feed."""
    user = await get_current_user(request, db)
    g = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(
        g,
        game,
        f"/analysis/rounds/{round_num}",
    ):
        return redirect
    players, actions = await _insight_records(db, g)
    played = sorted({a.round for a in actions})
    if round_num not in played:
        raise HTTPException(404)
    detail = round_detail(round_num, players, actions)
    return templates.TemplateResponse(
        request,
        "analysis_round.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": g,
            "game_theme": _game_theme(g),
            "detail": detail,
            "played": played,
        },
    )


@router.get("/games/{match_id}/analysis/rounds/{round_num}", include_in_schema=False)
async def legacy_game_analysis_round_redirect(
    match_id: Annotated[str, Path()],
    round_num: Annotated[int, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix=f"/analysis/rounds/{round_num}")
