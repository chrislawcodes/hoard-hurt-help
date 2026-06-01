"""HTMX-served web routes: lobby, join, my games, per-player dashboard."""

import logging
import random
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
from app.games import get as get_game_module
from app.games.base import GameError
from app.models.bot import Bot
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.user import User
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["web"])

logger = logging.getLogger(__name__)


async def _player_count(db, game_id: str) -> int:
    """Active players only — a pulled-out (left) bot frees its seat."""
    return len(
        (
            await db.execute(
                select(Player).where(Player.game_id == game_id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )


def _is_admin(user: User | None) -> bool:
    return user is not None and user.email.lower() in settings.admin_emails_set


# A finished game named like this is a deploy smoke test, not a real match —
# keep it out of the public front door (featured replay + recent list).
_TEST_NAME_PREFIX = "prod smoke"


def _is_showcase(view: dict) -> bool:
    """Real, watchable game: had a full table and isn't a smoke test."""
    return view["player_count"] >= 3 and not view["name"].strip().lower().startswith(
        _TEST_NAME_PREFIX
    )


async def _top_standings(db, game_id: str, limit: int = 3) -> list[dict]:
    """Top-N active players by round-wins then round-score, ranked from 1."""
    players = (
        (
            await db.execute(
                select(Player).where(Player.game_id == game_id, Player.left_at.is_(None))
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


async def _final_round_moments(db, game_id: str, limit: int = 14) -> list[dict]:
    """The last round of a finished game as an ordered list of moves — the
    climax, used as the auto-playing replay on the lobby. Empty if no turns."""
    from app.models.turn import Turn, TurnSubmission

    players = (
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
    )
    names = {p.id: p.agent_id for p in players}
    turns = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.game_id == game_id, Turn.resolved_at.is_not(None))
                .order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    if not turns:
        return []
    final_round = turns[-1].round  # turns are ordered ascending by (round, turn)
    moments: list[dict] = []
    for t in (t for t in turns if t.round == final_round):
        subs = (
            (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == t.id)))
            .scalars()
            .all()
        )
        for s in subs:
            actor = names.get(s.player_id)
            if not actor:
                continue
            target = names.get(s.target_player_id) if s.target_player_id else None
            moments.append(
                {
                    "round": t.round,
                    "turn": t.turn,
                    "agent_id": actor,
                    "action": s.action,
                    "target_id": target,
                    "message": s.message,
                }
            )
    return moments[-limit:]


async def _featured_replay(db, completed_views: list[dict]) -> dict | None:
    """Pick a watchable finished game at random from the most recent few, so a
    repeat visitor sees variety, and load its final round as a story. Returns
    None if nothing qualifies (caller falls back to the explainer-only hero)."""
    candidates = [v for v in completed_views if _is_showcase(v)][:5]
    random.shuffle(candidates)
    for chosen in candidates:
        moments = await _final_round_moments(db, chosen["id"])
        if not moments:
            continue  # no resolved turns to replay — try the next candidate
        return {
            "id": chosen["id"],
            "name": chosen["name"],
            "winner_agent_id": chosen["winner_agent_id"],
            "round": moments[0]["round"],
            "standings": await _top_standings(db, chosen["id"], 3),
            "moments": moments,
        }
    return None


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
    lobby itself lives one level down at `/play/hoard-hurt-help`.
    """
    user = await get_current_user(request, db)
    all_games = (
        (await db.execute(select(Game).order_by(Game.scheduled_start.desc()))).scalars().all()
    )
    live: list[dict] = []
    completed: list[dict] = []
    for g in all_games:
        view = {
            "id": g.id,
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

    # Hero match card: a real finished game's final round (None → explainer-only hero).
    featured = await _featured_replay(db, completed)

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
            "featured": featured,
            "standings": standings,
            "standings_game": standings_game,
            "has_live": bool(live),
        },
    )


@router.get("/play/hoard-hurt-help", response_class=HTMLResponse)
async def hoard_hurt_help_lobby(request: Request, db: DbSession):
    """Hoard·Hurt·Help lobby (game #1). The platform front page lives at `/`."""
    user = await get_current_user(request, db)
    all_games = (
        (await db.execute(select(Game).order_by(Game.scheduled_start.desc()))).scalars().all()
    )
    live = []
    upcoming = []
    recent = []
    for g in all_games:
        view = {
            "id": g.id,
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
        elif g.state in (GameState.SCHEDULED, GameState.REGISTERING):
            upcoming.append(view)
        elif g.state == GameState.COMPLETED:
            if g.winner_player_id:
                winner = (
                    await db.execute(select(Player).where(Player.id == g.winner_player_id))
                ).scalar_one_or_none()
                view["winner_agent_id"] = winner.agent_id if winner else None
            recent.append(view)

    # Marquee = the most-progressed live game (rounds, then turns).
    live.sort(key=lambda v: (v["current_round"], v["current_turn"]), reverse=True)
    # When nothing is live, feature a finished game's final round as a replay.
    featured = None if live else await _featured_replay(db, recent)
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
            "featured": featured,
        },
    )


async def _game_view_context(request: Request, db, game_id: str) -> dict:
    """Build the shared context for the game viewer page and its live fragment."""
    from app.models.turn import Turn, TurnSubmission

    user = await get_current_user(request, db)
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    players = (
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
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
                .where(Turn.game_id == game_id, Turn.resolved_at.is_not(None))
                .order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    history = []
    for seq, t in enumerate(turns, start=1):
        subs = (
            (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == t.id)))
            .scalars()
            .all()
        )
        actions = []
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if not actor:
                continue
            actor_delta, target_delta = _move_effect_for(g.game_type, s.action)
            actions.append(
                {
                    "agent_id": actor.agent_id,
                    "action": s.action,
                    "target_id": target.agent_id if target else None,
                    "message": s.message,
                    # Nominal per-move effect, attributed to who it lands on.
                    "actor_delta": actor_delta,
                    "target_delta": target_delta,
                    "was_defaulted": s.was_defaulted,
                }
            )
        history.append({"seq": seq, "round": t.round, "turn": t.turn, "actions": actions})

    # Group resolved turns by round for the round-navigation viewer. Rounds are
    # ordered newest-first, and turns within a round newest-first — this matches
    # the previous flat "newest first" feed ordering. `history` is already sorted
    # ascending by (round, turn), so we group in order then reverse.
    rounds: list[dict] = []
    for h in history:
        if not rounds or rounds[-1]["round"] != h["round"]:
            rounds.append({"round": h["round"], "turns": []})
        rounds[-1]["turns"].append(h)
    for r in rounds:
        r["turns"].reverse()
    rounds.reverse()
    max_played_round = rounds[0]["round"] if rounds else 0

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
        "scoreboard": scoreboard,
        "history": history,
        "rounds": rounds,
        "max_played_round": max_played_round,
        "winner_agent_id": winner_agent_id,
    }


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_viewer(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    ctx = await _game_view_context(request, db, game_id)
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/games/{game_id}/live", response_class=HTMLResponse)
async def game_live_fragment(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    """Server-rendered live region. SSE events trigger the page to re-fetch this."""
    ctx = await _game_view_context(request, db, game_id)
    return templates.TemplateResponse(request, "fragments/live_region.html", ctx)


async def _insight_records(db, game: Game) -> tuple[list[PlayerRecord], list[ActionRecord]]:
    """Map DB rows to the DB-free records the insights engine consumes."""
    from app.models.turn import Turn, TurnSubmission

    players = (
        (await db.execute(select(Player).where(Player.game_id == game.id))).scalars().all()
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
                .where(Turn.game_id == game.id, Turn.resolved_at.is_not(None))
                .order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    if not turns:
        return player_records, []
    turn_by_id = {t.id: t for t in turns}
    subs = (
        (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id.in_([t.id for t in turns])
                )
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
                message=s.message,
                points_delta=s.points_delta,
                round_score_after=s.round_score_after,
                was_defaulted=s.was_defaulted,
            )
        )
    return player_records, actions


@router.get("/games/{game_id}/analysis", response_class=HTMLResponse)
async def game_analysis(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    """Season home for the spectator analysis — the round-win race, results,
    grudges, and (when live) a peek into the current round."""
    user = await get_current_user(request, db)
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
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
            "overview": overview,
            "zero_wins": zero_wins,
            "live_peek": live_peek,
        },
    )


@router.get("/games/{game_id}/analysis/rounds/{round_num}", response_class=HTMLResponse)
async def game_analysis_round(
    game_id: Annotated[str, Path()],
    round_num: Annotated[int, Path()],
    request: Request,
    db: DbSession,
):
    """Drill-in for one round: leaderboard-from-0, mood, alliances, event feed."""
    user = await get_current_user(request, db)
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
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
            "detail": detail,
            "played": played,
        },
    )


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


# Chained-session agent runners — one per CLI provider. Each drives the bot as a
# single resumed agent session per game, so it remembers the whole match and
# only calls the model on the bot's turn. Allowlisted by exact filename below.
_AGENT_RUNNERS: dict[str, FsPath] = {
    "agentludum_agent.py": FsPath("scripts/agentludum_agent.py"),
    "agentludum_agent_codex.py": FsPath("scripts/agentludum_agent_codex.py"),
    "agentludum_agent_gemini.py": FsPath("scripts/agentludum_agent_gemini.py"),
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


@router.get("/games/{game_id}/join", response_class=HTMLResponse)
async def join_form(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    user = await get_current_user(request, db)
    if user is None:
        # Send through OAuth, returning back to this URL.
        return RedirectResponse(
            url=f"/auth/google/login?next=/games/{game_id}/join",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if game is None:
        raise HTTPException(404)

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
    module = get_game_module(game.game_type)
    presets = [asdict(p) for p in module.strategy_presets()]
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": game,
            "player_count": await _player_count(db, game.id),
            "bots": bots,
            "presets": presets,
            "strategy_prompt": module.default_strategy(),
            "base_url": settings.base_url,
            "error": None,
        },
    )


@router.post("/games/{game_id}/join")
async def join_submit(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    bot_id: Annotated[int, Form()],
    display_name: Annotated[str, Form()],
    strategy_prompt: Annotated[str, Form()] = "",
):
    """Enter one of the user's bots into a game. No credential is issued."""
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if game is None:
        raise HTTPException(404)
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Game not open for registration.")

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
                Player.game_id == game.id,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    name_taken = (
        await db.execute(
            select(Player).where(
                Player.game_id == game.id,
                Player.agent_id == display_name,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    count = await _player_count(db, game.id)

    error: str | None = None
    code = status.HTTP_400_BAD_REQUEST
    if not name_ok:
        error = "Name must be 1–32 letters, numbers, or underscores."
    elif already_in is not None:
        error, code = "That bot is already in this game.", status.HTTP_409_CONFLICT
    elif name_taken is not None:
        error = "That display name is already taken in this game."
    elif count >= game.max_players:
        error, code = "Game is full.", status.HTTP_409_CONFLICT
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
        presets = [asdict(p) for p in get_game_module(game.game_type).strategy_presets()]
        return templates.TemplateResponse(
            request,
            "join.html",
            {
                "user": user,
                "is_admin": _is_admin(user),
                "game": game,
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
        game_id=game.id,
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
    seed = strategy_prompt.strip() or get_game_module(game.game_type).default_strategy()
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
        g = (await db.execute(select(Game).where(Game.id == p.game_id))).scalar_one()
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

    game = (await db.execute(select(Game).where(Game.id == player.game_id))).scalar_one()
    presets = [asdict(p) for p in get_game_module(game.game_type).strategy_presets()]

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
    game = (await db.execute(select(Game).where(Game.id == player.game_id))).scalar_one()
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
    game = (await db.execute(select(Game).where(Game.id == player.game_id))).scalar_one()
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Cannot leave after start.")
    player.left_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/me/games", status_code=status.HTTP_303_SEE_OTHER)
