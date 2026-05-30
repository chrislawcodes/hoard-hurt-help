"""HTMX-served web routes: lobby, join, my games, per-player dashboard."""

import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path as FsPath
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession, get_current_user, require_user
from app.engine.game_insights import round_detail, season_overview
from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.rules import (
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    STRATEGY_PRESETS,
)
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


def _move_effect(action: str) -> tuple[int, int | None]:
    """Nominal point effect of one move, split into (actor_delta, target_delta).

    This is what the move is *worth* by the rules — HOARD +2 to self, HELP +4 to
    the target, HURT -4 to the target. It is shown per-move in the watch feed so
    viewers see who is affected. It is deliberately NOT the player's net change
    for the turn (that can fold in others' moves, the mutual-help bonus, and the
    score floor); the running scoreboard reflects those actual totals.
    """
    if action == "HOARD":
        return HOARD_POINTS, None
    if action == "HELP":
        return 0, HELP_POINTS
    if action == "HURT":
        return 0, -HURT_POINTS
    return 0, None


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: DbSession):
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
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "live_games": live,
            "upcoming_games": upcoming,
            "recent_games": recent[:20],
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
            actor_delta, target_delta = _move_effect(s.action)
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
    # stable key was shown once when it was created (see /me/bots).
    bots = (
        (await db.execute(select(Bot).where(Bot.user_id == user.id).order_by(Bot.name)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": game,
            "player_count": await _player_count(db, game.id),
            "bots": bots,
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
):
    """Enter one of the user's bots into a game. No credential is issued."""
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if game is None:
        raise HTTPException(404)
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Game not open for registration.")

    bot = (
        await db.execute(select(Bot).where(Bot.id == bot_id, Bot.user_id == user.id))
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
            (await db.execute(select(Bot).where(Bot.user_id == user.id).order_by(Bot.name)))
            .scalars()
            .all()
        )
        return templates.TemplateResponse(
            request,
            "join.html",
            {
                "user": user,
                "is_admin": _is_admin(user),
                "game": game,
                "player_count": count,
                "bots": bots,
                "base_url": settings.base_url,
                "error": error,
            },
            status_code=code,
        )

    player = Player(
        game_id=game.id,
        user_id=bot.user_id,
        bot_id=bot.id,
        agent_id=display_name,
    )
    db.add(player)
    await db.flush()
    # Seed each new player with a randomly chosen preset so a lobby of bots that
    # never edit their strategy still gets a varied mix, not all the same one.
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=random.choice(STRATEGY_PRESETS)["prompt"],
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
            "presets": STRATEGY_PRESETS,
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
