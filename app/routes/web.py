"""HTMX-served web routes: lobby, join, my games, per-player dashboard."""

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path as FsPath
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession, get_current_user, require_user
from app.engine.game_insights import round_detail, season_overview
from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.match_id_rewrite import match_id_candidates
from app.engine.scheduler import cancel_overdue_unfilled_games
from app.games import get as get_game_module
from app.games.base import GameError, GameTheme
from app.models.bot import Bot
from app.models.match import Match, GameState
from app.models.player import Player
from app.request_logging import set_request_trace_context
from app.models.strategy_prompt import StrategyPrompt
from app.models.user import User
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["web"])

logger = logging.getLogger(__name__)


async def _player_count(db, match_id: str) -> int:
    """Active players only — a pulled-out (left) bot frees its seat."""
    return len(
        (
            await db.execute(
                select(Player).where(Player.match_id == match_id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )


def _is_admin(user: User | None) -> bool:
    return user is not None and user.email.lower() in settings.admin_emails_set


async def _upcoming_views(db) -> list[dict]:
    """Scheduled/registering games as the lobby's 'Upcoming' cards.

    Shared by the lobby page and the polled `/upcoming` fragment so both render
    the exact same list. Newest scheduled_start first, matching the page order.
    """
    games = (
        (
            await db.execute(
                select(Match)
                .where(Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]))
                .order_by(Match.scheduled_start.desc())
            )
        )
        .scalars()
        .all()
    )
    views: list[dict] = []
    for g in games:
        views.append(
            {
                "id": g.id,
                "game_type": g.game,
                "name": g.name,
                "scheduled_start": g.scheduled_start.isoformat(),
                "max_players": g.max_players,
                "player_count": await _player_count(db, g.id),
            }
        )
    return views


def _game_theme(game: Match) -> GameTheme | None:
    """A game's content tint for its pages (lobby, viewer, analysis, join, etc.).

    base.html stamps it on <main data-game>, so the shared chrome is untouched.
    Unknown game types fall back to the platform-neutral look (no tint).
    """
    try:
        return get_game_module(game.game).theme()
    except GameError:
        return None


def _match_url(match: Match, suffix: str = "") -> str:
    return f"/games/{match.game}/matches/{match.id}{suffix}"


async def _redirect_to_match(
    db,
    legacy_match_id: str,
    *,
    suffix: str = "",
) -> RedirectResponse:
    match = None
    for candidate_match_id in match_id_candidates(legacy_match_id):
        match = (
            await db.execute(select(Match).where(Match.id == candidate_match_id))
        ).scalar_one_or_none()
        if match is not None:
            break
    if match is None:
        raise HTTPException(404)
    return RedirectResponse(url=_match_url(match, suffix), status_code=status.HTTP_301_MOVED_PERMANENTLY)


# A finished game named like this is a deploy smoke test, not a real match —
# keep it out of the public front door (featured replay + recent list).
_TEST_NAME_PREFIX = "prod smoke"


def _is_showcase(view: dict) -> bool:
    """Real, watchable game: had a full table and isn't a smoke test."""
    return view["player_count"] >= 3 and not view["name"].strip().lower().startswith(
        _TEST_NAME_PREFIX
    )


async def _top_standings(db, match_id: str, limit: int = 3) -> list[dict]:
    """Top-N active players by round-wins then round-score, ranked from 1."""
    players = (
        (
            await db.execute(
                select(Player).where(Player.match_id == match_id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    rows = sorted(
        (
            {
                "agent_id": p.agent_id,
                "round_score": p.current_round_score,
                "round_wins": p.total_round_wins,
            }
            for p in players
        ),
        key=lambda r: (-r["round_wins"], -r["round_score"]),
    )[:limit]
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


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


def _move_effect_for(game_type: str, action: str) -> tuple[int, int | None]:
    """Nominal per-move effect for the watch feed, split into (actor_delta, target_delta).

    Delegates to the game module so the viewer carries no game-specific scoring.
    This is what the move is *worth* by that game's rules (e.g. PD: HOARD +2 to
    self, HELP +4 to the target, HURT -4 to the target) — shown per-move so
    viewers see who each move lands on. It is deliberately NOT the player's net
    change for the turn (which folds in others' moves, bonuses, and the floor);
    the running scoreboard reflects those actual totals. An unknown game type
    falls back to no displayed delta rather than crashing the viewer.
    """
    try:
        return get_game_module(game_type).move_effect(action)
    except GameError:
        return 0, None


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


def _build_rc_data(scoreboard: list[dict], history: list[dict]) -> str:
    """Serialize game history as the robot-circle viewer JSON format."""
    agents = [r["agent_id"] for r in scoreboard]

    turns = []
    for h in history:
        rc_actions = []
        for a in h["actions"]:
            rc_actions.append({
                "agent": a["agent_id"],
                "action": a["action"],
                "target": a["target_id"],
                "delta": a["display_delta"],
                "mutual": a["mutual"],
                "betrayal": a["betrayal"],
                "missed": a["was_defaulted"],
                "msg": (a.get("message") or "").strip(),
            })

        spot: set[str] = set()
        for a in rc_actions:
            spot.add(a["agent"])
            if a["target"]:
                spot.add(a["target"])

        betrayals = [a for a in rc_actions if a["betrayal"]]
        mutuals   = [a for a in rc_actions if a["mutual"]]
        hurts     = [a for a in rc_actions if a["action"] == "HURT" and a["target"]]
        helps     = [a for a in rc_actions if a["action"] == "HELP" and not a["mutual"] and a["target"]]
        missed    = [a for a in rc_actions if a["missed"]]

        if betrayals:
            b = betrayals[0]
            badge, cap = "Betrayal", f"{b['agent']} turns on former ally {b['target']}."
        elif mutuals:
            pair = sorted({a["agent"] for a in mutuals} | {a["target"] for a in mutuals})
            if len(pair) == 2:
                badge, cap = "The Pact", f"{pair[0]} and {pair[1]} lock in a mutual pact — +8 each."
            else:
                badge, cap = "The Pact", "Mutual pacts lock in — +8 each."
        elif hurts:
            h0 = hurts[0]
            badge, cap = "Strike", f"{h0['agent']} strikes {h0['target']}."
        elif helps:
            badge = "Help"
            cap = (f"{helps[0]['agent']} helps {helps[0]['target']}." if len(helps) == 1
                   else "Gifts change hands — one-way help around the circle.")
        elif missed and len(missed) == len(rc_actions):
            badge, cap = "No-show", f"{missed[0]['agent']} missed its turn — defaulted to Hoard."
        else:
            badge, cap = "Hoard", "A quiet turn — everyone banks a coin."

        talk = [
            {"agent": m["agent_id"], "text": m["text"].strip()}
            for m in h["messages"]
            if m["text"].strip()
        ]

        turns.append({
            "round": h["round"],
            "turn": h["turn"],
            "badge": badge,
            "cap": cap,
            "spotlight": sorted(spot),
            "actions": rc_actions,
            "talk": talk,
        })

    return json.dumps({
        "agents": agents,
        "turns": turns,
        "max_round": max((t["round"] for t in turns), default=0),
        "sample": False,
    }, ensure_ascii=False)


async def _game_view_context(request: Request, db, match_id: str) -> dict:
    """Build the shared context for the game viewer page and its live fragment."""
    from app.models.turn import Turn, TurnMessage, TurnSubmission

    user = await get_current_user(request, db)
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    players = (
        (await db.execute(select(Player).where(Player.match_id == match_id))).scalars().all()
    )
    players_by_id = {p.id: p for p in players}

    scoreboard = sorted(
        (
            {
                "agent_id": p.agent_id,
                "round_score": p.current_round_score,
                "round_wins": p.total_round_wins,
            }
            for p in players
        ),
        key=lambda r: (-r["round_wins"], -r["round_score"]),
    )
    for i, row in enumerate(scoreboard, start=1):
        row["rank"] = i

    turns = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.match_id == match_id, Turn.resolved_at.is_not(None))
                .order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    history = []
    turn_ids = [t.id for t in turns]
    messages_by_turn: dict[int, list[TurnMessage]] = {}
    if turn_ids:
        messages = (
            (
                await db.execute(
                    select(TurnMessage)
                    .where(TurnMessage.turn_id.in_(turn_ids))
                    .order_by(TurnMessage.turn_id, TurnMessage.submitted_at, TurnMessage.id)
                )
            )
            .scalars()
            .all()
        )
        for message in messages:
            messages_by_turn.setdefault(message.turn_id, []).append(message)

    subs_by_turn: dict[int, list[TurnSubmission]] = {}
    if turn_ids:
        subs = (
            (
                await db.execute(
                    select(TurnSubmission)
                    .where(TurnSubmission.turn_id.in_(turn_ids))
                    .order_by(
                        TurnSubmission.turn_id,
                        TurnSubmission.submitted_at,
                        TurnSubmission.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for sub in subs:
            subs_by_turn.setdefault(sub.turn_id, []).append(sub)

    # Per-turn pact/betrayal signals for the replay. A "pact" is a mutual HELP in
    # the same turn; a "betrayal" is a HURT aimed at last turn's pact partner.
    prev_mutual: set[frozenset[str]] = set()
    for seq, t in enumerate(turns, start=1):
        subs = subs_by_turn.get(t.id, [])
        turn_messages = messages_by_turn.get(t.id, [])
        if turn_messages:
            messages = [
                {
                    "agent_id": players_by_id[msg.player_id].agent_id,
                    "text": msg.text,
                    "thinking": msg.thinking,
                    "was_defaulted": msg.was_defaulted,
                }
                for msg in turn_messages
                if msg.player_id in players_by_id
            ]
        else:
            messages = [
                {
                    "agent_id": players_by_id[s.player_id].agent_id,
                    "text": s.message,
                    "thinking": "",
                    "was_defaulted": s.was_defaulted,
                }
                for s in subs
                if s.player_id in players_by_id
            ]
        actions = []
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if not actor:
                continue
            actor_delta, target_delta = _move_effect_for(g.game, s.action)
            actions.append(
                {
                    "agent_id": actor.agent_id,
                    "action": s.action,
                    "target_id": target.agent_id if target else None,
                    # Nominal per-move effect, attributed to who it lands on.
                    "actor_delta": actor_delta,
                    "target_delta": target_delta,
                    "thinking": s.thinking,
                    "was_defaulted": s.was_defaulted,
                    "mutual": False,
                    "betrayal": False,
                }
            )

        # Tag this turn's pacts (mutual HELP) and betrayals (HURT on last turn's
        # pact partner), so the feed can mark them without re-deriving in JS.
        helps = {
            a["agent_id"]: a["target_id"]
            for a in actions
            if a["action"] == "HELP" and a["target_id"]
        }
        this_mutual: set[frozenset[str]] = set()
        for a in actions:
            tgt = a["target_id"]
            if not tgt:
                continue
            pair = frozenset((a["agent_id"], tgt))
            if a["action"] == "HELP" and helps.get(tgt) == a["agent_id"]:
                a["mutual"] = True
                this_mutual.add(pair)
            elif a["action"] == "HURT" and pair in prev_mutual:
                a["betrayal"] = True
        prev_mutual = this_mutual

        messages_by_agent = {m["agent_id"]: m for m in messages}
        for a in actions:
            paired_message = messages_by_agent.get(a["agent_id"])
            if paired_message is not None:
                a["message"] = paired_message["text"]
                a["message_thinking"] = paired_message["thinking"]
                a["message_was_defaulted"] = paired_message["was_defaulted"]
            else:
                a["message"] = ""
                a["message_thinking"] = ""
                a["message_was_defaulted"] = True

            if a["action"] == "HOARD":
                a["display_action"] = "Hoard"
                a["display_delta"] = a["actor_delta"]
            elif a["action"] == "HELP":
                a["display_action"] = "Help"
                a["display_delta"] = 8 if a["mutual"] else a["target_delta"]
            else:
                a["display_action"] = "HURT"
                a["display_delta"] = a["target_delta"]

        history.append(
            {
                "seq": seq,
                "round": t.round,
                "turn": t.turn,
                "messages": messages,
                "actions": actions,
            }
        )

    # Keep the server data in chronological order. The template reverses it for
    # the newest-first feed while round navigation can still reason about order.
    rounds: list[dict] = []
    for h in history:
        if not rounds or rounds[-1]["round"] != h["round"]:
            rounds.append({"round": h["round"], "turns": []})
        rounds[-1]["turns"].append(h)
    max_played_round = rounds[-1]["round"] if rounds else 0

    winner_agent_id = None
    if g.winner_player_id:
        winner = (
            await db.execute(select(Player).where(Player.id == g.winner_player_id))
        ).scalar_one_or_none()
        winner_agent_id = winner.agent_id if winner else None

    return {
        "user": user,
        "is_admin": _is_admin(user),
        "game": g,
        "game_theme": _game_theme(g),
        "scoreboard": scoreboard,
        "history": history,
        "rounds": rounds,
        "max_played_round": max_played_round,
        "winner_agent_id": winner_agent_id,
    }


@router.get("/games/{game}/matches/{match_id}", response_class=HTMLResponse)
async def game_viewer(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    ctx = await _game_view_context(request, db, match_id)
    if ctx["game"].game != game:
        return RedirectResponse(
            url=_match_url(ctx["game"]), status_code=status.HTTP_301_MOVED_PERMANENTLY
        )
    ctx["rc_data"] = _build_rc_data(ctx["scoreboard"], ctx["history"])
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/games/{game}/matches/{match_id}/live", response_class=HTMLResponse)
async def game_live_fragment(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    """Server-rendered live region. SSE events trigger the page to re-fetch this."""
    ctx = await _game_view_context(request, db, match_id)
    if ctx["game"].game != game:
        return RedirectResponse(
            url=_match_url(ctx["game"], "/live"),
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
        )
    return templates.TemplateResponse(request, "fragments/live_region.html", ctx)


@router.get("/games/{match_id}/live", include_in_schema=False)
async def legacy_game_live_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/live")


async def _insight_records(db, game: Match) -> tuple[list[PlayerRecord], list[ActionRecord]]:
    """Map DB rows to the DB-free records the insights engine consumes."""
    from app.models.turn import Turn, TurnMessage, TurnSubmission

    players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    player_records = [
        PlayerRecord(
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            total_score=p.total_round_score,
            round_wins=p.total_round_wins,
        )
        for p in players
    ]
    name_by_id = {p.id: p.agent_id for p in players}
    turns = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.match_id == game.id, Turn.resolved_at.is_not(None))
                .order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    if not turns:
        return player_records, []
    turn_by_id = {t.id: t for t in turns}
    turn_ids = [t.id for t in turns]
    message_text_by_turn_player: dict[tuple[int, int], str] = {}
    if turn_ids:
        for msg in (
            (
                await db.execute(
                    select(TurnMessage).where(TurnMessage.turn_id.in_(turn_ids))
                )
            )
            .scalars()
            .all()
        ):
            message_text_by_turn_player[(msg.turn_id, msg.player_id)] = msg.text
    subs = []
    if turn_ids:
        subs = (
            (
                await db.execute(
                    select(TurnSubmission).where(TurnSubmission.turn_id.in_(turn_ids))
                )
            )
            .scalars()
            .all()
        )
    actions: list[ActionRecord] = []
    for s in subs:
        t = turn_by_id[s.turn_id]
        target = name_by_id.get(s.target_player_id) if s.target_player_id else None
        actions.append(
            ActionRecord(
                round=t.round,
                turn=t.turn,
                actor_id=name_by_id[s.player_id],
                action=cast(Action, s.action),
                target_id=target,
                message=message_text_by_turn_player.get((s.turn_id, s.player_id), s.message),
                points_delta=s.points_delta,
                round_score_after=s.round_score_after,
                was_defaulted=s.was_defaulted,
            )
        )
    return player_records, actions


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
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    if g.game != game:
        return RedirectResponse(
            url=_match_url(g, "/analysis"), status_code=status.HTTP_301_MOVED_PERMANENTLY
        )
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
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    if g.game != game:
        return RedirectResponse(
            url=_match_url(g, f"/analysis/rounds/{round_num}"),
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
        )
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


_DOCS_DIR = FsPath("docs")
_GUIDE_NAME = re.compile(r"^[a-z0-9-]+$")


@router.get("/guide/{name}", response_class=HTMLResponse)
async def guide(name: Annotated[str, Path()], request: Request, db: DbSession):
    """Render a setup doc from docs/<name>.md inside the site chrome."""
    if not _GUIDE_NAME.match(name):
        raise HTTPException(404)
    path = _DOCS_DIR / f"{name}.md"
    if not path.is_file():
        raise HTTPException(404)
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        "guide.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "title": name.replace("-", " ").title(),
            "body": path.read_text(encoding="utf-8"),
        },
    )


_RUNNER_PATH = FsPath("scripts/agentludum_bot.py")


@router.get("/agentludum_bot.py", include_in_schema=False)
async def runner_script() -> FileResponse:
    """Serve the bot runner so the one-line `curl` in the setup message fetches it.

    Single source of truth: this streams the repo's scripts/agentludum_bot.py, so
    the downloaded runner always matches this server's version.
    """
    if not _RUNNER_PATH.is_file():
        raise HTTPException(404)
    return FileResponse(
        _RUNNER_PATH, media_type="text/x-python", filename="agentludum_bot.py"
    )


# Chained-session agent runner. ONE script now drives every CLI provider — it
# reads the bot's configured provider from the server and calls the matching CLI
# (claude/codex/gemini). The old per-provider filenames are kept as aliases so an
# older setup message still fetches a working runner; they all serve the same file.
# Allowlisted by exact filename below.
_UNIFIED_RUNNER = FsPath("scripts/agentludum_agent.py")
_AGENT_RUNNERS: dict[str, FsPath] = {
    "agentludum_agent.py": _UNIFIED_RUNNER,
    "agentludum_agent_codex.py": _UNIFIED_RUNNER,
    "agentludum_agent_gemini.py": _UNIFIED_RUNNER,
}


@router.get("/runners/{name}", include_in_schema=False)
async def agent_runner_script(name: Annotated[str, Path()]) -> FileResponse:
    """Serve a chained-session agent runner so the setup `curl` fetches it.

    Allowlisted by exact filename — the path never comes from the request, so
    there's no traversal surface. Single source of truth: this streams the
    repo's scripts/<name>, so the downloaded runner always matches this server.
    """
    path = _AGENT_RUNNERS.get(name)
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/x-python", filename=name)


@router.get("/games/{match_id}/join", response_class=HTMLResponse)
async def legacy_join_form_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/join")


@router.post("/games/{match_id}/join", include_in_schema=False)
async def legacy_join_submit_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return RedirectResponse(
        url=(await _redirect_to_match(db, match_id, suffix="/join")).headers["location"],
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )


@router.get("/games/{game}/matches/{match_id}/join", response_class=HTMLResponse)
async def join_form(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    user = await get_current_user(request, db)
    if user is None:
        # Send through OAuth, returning back to this URL.
        return RedirectResponse(
            url=f"/auth/google/login?next=/games/{game}/matches/{match_id}/join",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    set_request_trace_context(request, match_id=match_id, stage="join_form")
    match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise HTTPException(404)
    if match.game != game:
        return RedirectResponse(
            url=_match_url(match, "/join"), status_code=status.HTTP_301_MOVED_PERMANENTLY
        )

    # Entry is "pick one of your bots" — no per-game key is issued. The bot's
    # stable key was shown once when it was created (see /me/bots). Archived
    # (deleted) bots are excluded — they can't enter games.
    bots = (
        (
            await db.execute(
                select(Bot)
                .where(Bot.user_id == user.id, Bot.archived_at.is_(None))
                .order_by(Bot.name)
            )
        )
        .scalars()
        .all()
    )
    module = get_game_module(match.game)
    presets = [asdict(p) for p in module.strategy_presets()]
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": match,
            "game_theme": _game_theme(match),
            "player_count": await _player_count(db, match.id),
            "bots": bots,
            "presets": presets,
            "strategy_prompt": module.default_strategy(),
            "base_url": settings.base_url,
            "error": None,
        },
    )


@router.post("/games/{game}/matches/{match_id}/join")
async def join_submit(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    bot_id: Annotated[int, Form()],
    display_name: Annotated[str, Form()],
    strategy_prompt: Annotated[str, Form()] = "",
):
    """Enter one of the user's bots into a game. No credential is issued."""
    set_request_trace_context(
        request, match_id=match_id, stage="join_submit", bot_id=bot_id, display_name=display_name
    )
    match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise HTTPException(404)
    if match.game != game:
        return RedirectResponse(
            url=_match_url(match, "/join"), status_code=status.HTTP_308_PERMANENT_REDIRECT
        )
    if match.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Match not open for registration.")

    bot = (
        await db.execute(
            select(Bot).where(
                Bot.id == bot_id,
                Bot.user_id == user.id,
                Bot.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if bot is None:
        raise HTTPException(404, detail="Bot not found.")

    # Validate entry: name shape, one player per (bot, game), unique name, capacity.
    name_ok = bool(re.fullmatch(r"[a-zA-Z0-9_]{1,32}", display_name))
    already_in = (
        await db.execute(
            select(Player).where(
                Player.bot_id == bot.id,
                Player.match_id == match.id,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    name_taken = (
        await db.execute(
            select(Player).where(
                Player.match_id == match.id,
                Player.agent_id == display_name,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    count = await _player_count(db, match.id)

    error: str | None = None
    code = status.HTTP_400_BAD_REQUEST
    if not name_ok:
        error = "Name must be 1–32 letters, numbers, or underscores."
    elif already_in is not None:
        error, code = "That bot is already in this game.", status.HTTP_409_CONFLICT
    elif name_taken is not None:
        error = "That display name is already taken in this game."
    elif count >= match.max_players:
        error, code = "Match is full.", status.HTTP_409_CONFLICT
    if error is not None:
        bots = (
            (
                await db.execute(
                    select(Bot)
                    .where(Bot.user_id == user.id, Bot.archived_at.is_(None))
                    .order_by(Bot.name)
                )
            )
            .scalars()
            .all()
        )
        presets = [asdict(p) for p in get_game_module(match.game).strategy_presets()]
        return templates.TemplateResponse(
            request,
            "join.html",
            {
                "user": user,
                "is_admin": _is_admin(user),
                "game": match,
                "game_theme": _game_theme(match),
                "player_count": count,
                "bots": bots,
                "presets": presets,
                "strategy_prompt": strategy_prompt,
                "base_url": settings.base_url,
                "error": error,
            },
            status_code=code,
        )

    if bot.provider:
        _model_label = bot.provider.value + (f"/{bot.model}" if bot.model else "")
    else:
        _model_label = None
    player = Player(
        match_id=match.id,
        user_id=bot.user_id,
        bot_id=bot.id,
        agent_id=display_name,
        model_self_report=_model_label,
    )
    db.add(player)
    await db.flush()
    # Seed the player's per-game strategy from what they submitted at entry (a
    # preset they picked or text they wrote); blank falls back to the game's
    # default. Copy-at-entry: later edits on the player page don't rewrite this.
    seed = strategy_prompt.strip() or get_game_module(match.game).default_strategy()
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=seed,
            is_default=False,
        )
    )
    await db.commit()

    return RedirectResponse(
        url=f"/me/bots/{bot.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/me/games", response_class=HTMLResponse)
async def my_games(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    players = (
        (await db.execute(select(Player).where(Player.user_id == user.id))).scalars().all()
    )
    games = []
    for p in players:
        g = (await db.execute(select(Match).where(Match.id == p.match_id))).scalar_one()
        games.append(
            {
                "id": g.id,
                "name": g.name,
                "state": g.state,
                "agent_id": p.agent_id,
                "player_id": p.id,
            }
        )
    return templates.TemplateResponse(
        request,
        "my_games.html",
        {"user": user, "is_admin": _is_admin(user), "games": games},
    )


@router.get("/me/players/{player_id}", response_class=HTMLResponse)
async def player_dashboard(
    player_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    saved: bool = False,
):
    player = (
        await db.execute(
            select(Player).where(Player.id == player_id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(404, detail="Bot slot not found.")

    game = (await db.execute(select(Match).where(Match.id == player.match_id))).scalar_one()
    presets = [asdict(p) for p in get_game_module(game.game).strategy_presets()]

    latest_prompt = (
        await db.execute(
            select(StrategyPrompt)
            .where(StrategyPrompt.player_id == player.id)
            .order_by(StrategyPrompt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # The agent key is shown exactly once, right after it is issued — on join or
    # on an explicit re-issue (see reissue_agent_key). We only ever store the
    # argon2 hash, so we cannot show the key again on later visits. Crucially, we
    # do NOT regenerate the key on a plain dashboard visit: doing so silently
    # invalidated the key a bot was already configured with.
    fresh_key = request.session.pop(f"fresh_key_{player.id}", None)

    selected_ai = request.session.pop(f"ai_type_{player.id}", None)
    pre_game = game.state in (GameState.SCHEDULED, GameState.REGISTERING)

    return templates.TemplateResponse(
        request,
        "connection.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": game,
            "game_theme": _game_theme(game),
            "player": player,
            "agent_key": fresh_key,
            "strategy": latest_prompt.prompt_text if latest_prompt else "",
            "base_url": settings.base_url,
            "selected_ai": selected_ai,
            "presets": presets,
            "just_saved": saved,
            "can_edit_strategy": game.state != GameState.ACTIVE
            and game.state != GameState.COMPLETED,
            "can_leave": pre_game,
            "pre_game": pre_game,
        },
    )


# Key reissue moved to the bot level (POST /me/bots/{bot_id}/reissue) and is
# allowed at any time — see app/routes/bots_web.py. There is no per-player key.


@router.post("/me/players/{player_id}/strategy")
async def update_strategy(
    player_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    strategy_prompt: Annotated[str, Form()],
):
    player = (
        await db.execute(
            select(Player).where(Player.id == player_id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(404)
    game = (await db.execute(select(Match).where(Match.id == player.match_id))).scalar_one()
    if game.state in (GameState.ACTIVE, GameState.COMPLETED):
        raise HTTPException(409, detail="Strategy locked after game starts.")
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=strategy_prompt,
            is_default=False,
        )
    )
    await db.commit()
    return RedirectResponse(
        url=f"/me/players/{player.id}?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/me/players/{player_id}/leave")
async def web_leave(
    player_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    player = (
        await db.execute(
            select(Player).where(Player.id == player_id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(404)
    game = (await db.execute(select(Match).where(Match.id == player.match_id))).scalar_one()
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Cannot leave after start.")
    player.left_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/me/games", status_code=status.HTTP_303_SEE_OTHER)
