"""HTMX-served web routes: lobby, join, my games, per-game dashboard."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession, get_current_user, require_user
from app.engine.rules import DEFAULT_STRATEGY_PROMPT
from app.engine.tokens import generate_agent_key, hash_agent_key
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.user import User

router = APIRouter(tags=["web"])
from app.templating import templates  # shared instance with custom filters


async def _player_count(db, game_id: str) -> int:
    return len(
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
    )


def _is_admin(user: User | None) -> bool:
    return user is not None and user.email.lower() in settings.admin_emails_set


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


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_viewer(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    user = await get_current_user(request, db)
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    players = (
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
    )
    players_by_id = {p.id: p for p in players}
    scoreboard = [
        {
            "agent_id": p.agent_id,
            "round_score": p.current_round_score,
            "round_wins": p.total_round_wins,
        }
        for p in players
    ]
    # Build history.
    from app.models.turn import Turn, TurnSubmission

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
    for t in turns:
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
            actions.append(
                {
                    "agent_id": actor.agent_id,
                    "action": s.action,
                    "target_id": target.agent_id if target else None,
                    "message": s.message,
                    "points_delta": s.points_delta,
                }
            )
        history.append({"round": t.round, "turn": t.turn, "actions": actions})

    winner_agent_id = None
    if g.winner_player_id:
        winner = (
            await db.execute(select(Player).where(Player.id == g.winner_player_id))
        ).scalar_one_or_none()
        winner_agent_id = winner.agent_id if winner else None

    return templates.TemplateResponse(
        request,
        "game.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": g,
            "scoreboard": scoreboard,
            "history": history,
            "winner_agent_id": winner_agent_id,
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

    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": game,
            "default_prompt": DEFAULT_STRATEGY_PROMPT,
            "player_count": await _player_count(db, game.id),
            "error": None,
        },
    )


@router.post("/games/{game_id}/join")
async def join_submit(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    display_name: Annotated[str, Form()],
    strategy_prompt: Annotated[str, Form()],
):
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if game is None:
        raise HTTPException(404)
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Game not open for registration.")

    # Already joined?
    existing = (
        await db.execute(
            select(Player).where(Player.game_id == game.id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return RedirectResponse(
            url=f"/me/games/{game.id}", status_code=status.HTTP_303_SEE_OTHER
        )

    # Validate display name.
    name_taken = (
        await db.execute(
            select(Player).where(Player.game_id == game.id, Player.agent_id == display_name)
        )
    ).scalar_one_or_none()
    if name_taken is not None:
        return templates.TemplateResponse(
            request,
            "join.html",
            {
                "user": user,
                "is_admin": _is_admin(user),
                "game": game,
                "default_prompt": strategy_prompt,
                "player_count": await _player_count(db, game.id),
                "error": "That display name is already taken in this game.",
            },
            status_code=400,
        )

    # Count cap.
    if await _player_count(db, game.id) >= game.max_players:
        return templates.TemplateResponse(
            request,
            "join.html",
            {
                "user": user,
                "is_admin": _is_admin(user),
                "game": game,
                "default_prompt": strategy_prompt,
                "player_count": await _player_count(db, game.id),
                "error": "Game is full.",
            },
            status_code=409,
        )

    key = generate_agent_key()
    player = Player(
        game_id=game.id,
        user_id=user.id,
        agent_id=display_name,
        agent_key_hash=hash_agent_key(key),
    )
    db.add(player)
    await db.flush()
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=strategy_prompt,
            is_default=(strategy_prompt.strip() == DEFAULT_STRATEGY_PROMPT.strip()),
        )
    )
    await db.commit()

    # Stash the freshly issued key so we can show it once on the dashboard.
    request.session[f"fresh_key_{game.id}"] = key

    return RedirectResponse(
        url=f"/me/games/{game.id}", status_code=status.HTTP_303_SEE_OTHER
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
        games.append({"id": g.id, "name": g.name, "state": g.state, "agent_id": p.agent_id})
    return templates.TemplateResponse(
        request,
        "my_games.html",
        {"user": user, "is_admin": _is_admin(user), "games": games},
    )


@router.get("/me/games/{game_id}", response_class=HTMLResponse)
async def my_game_dashboard(
    game_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    player = (
        await db.execute(
            select(Player).where(Player.game_id == game_id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(404, detail="You haven't joined this game.")

    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()

    latest_prompt = (
        await db.execute(
            select(StrategyPrompt)
            .where(StrategyPrompt.player_id == player.id)
            .order_by(StrategyPrompt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    fresh_key = request.session.pop(f"fresh_key_{game.id}", None)

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
            "can_edit_strategy": game.state != GameState.ACTIVE
            and game.state != GameState.COMPLETED,
            "can_leave": game.state in (GameState.SCHEDULED, GameState.REGISTERING),
        },
    )


@router.post("/me/games/{game_id}/strategy")
async def update_strategy(
    game_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    strategy_prompt: Annotated[str, Form()],
):
    player = (
        await db.execute(
            select(Player).where(Player.game_id == game_id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(404)
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()
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
        url=f"/me/games/{game_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/me/games/{game_id}/leave")
async def web_leave(
    game_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    player = (
        await db.execute(
            select(Player).where(Player.game_id == game_id, Player.user_id == user.id)
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(404)
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Cannot leave after start.")
    player.left_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/me/games", status_code=status.HTTP_303_SEE_OTHER)
