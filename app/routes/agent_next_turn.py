"""Game-agnostic next-turn endpoint — the heart of the paste-once play loop.

A connected bot (via the MCP `get_next_turn` tool) calls this and gets its single
most-urgent open turn across ALL of its active games, or a `waiting` response.
The bot never has to know game ids up front.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.deps import DbSession, require_bot
from app.engine.next_turn import TurnCandidate, select_next_turn
from app.games import get as get_game_module
from app.models.bot import Bot
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.turn import Turn, TurnSubmission
from app.routes.agent_api import (
    _build_scoreboard,
    _group_into_turns,
    _load_action_records,
)
from app.schemas.agent import CurrentTurn, NextTurnWaiting, NextTurnYourTurn, TurnStatic

router = APIRouter(prefix="/api/agent", tags=["agent"])

_POLL_WHEN_WAITING = 5


def _as_aware(dt: datetime) -> datetime:
    """SQLite drops timezone info on read; normalize to UTC-aware for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@router.get("/next-turn")
async def next_turn(
    bot: Annotated[Bot, Depends(require_bot)],
    db: DbSession,
) -> NextTurnWaiting | NextTurnYourTurn:
    """Return the bot's single most-urgent open turn, or a waiting response.

    `require_bot` already rejects a paused bot with 403 BOT_PAUSED, so by the
    time we are here the bot is active.
    """
    players = (
        (
            await db.execute(
                select(Player).where(Player.bot_id == bot.id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    if not players:
        return NextTurnWaiting(
            reason="no_active_games", next_poll_after_seconds=_POLL_WHEN_WAITING
        )

    players_by_game = {p.game_id: p for p in players}
    games = (
        (
            await db.execute(
                select(Game).where(
                    Game.id.in_(list(players_by_game)), Game.state == GameState.ACTIVE
                )
            )
        )
        .scalars()
        .all()
    )
    if not games:
        return NextTurnWaiting(
            reason="no_active_games", next_poll_after_seconds=_POLL_WHEN_WAITING
        )

    # For each active game, find the latest open turn the bot still owes a move
    # on (exclude turns it already answered — otherwise the loop re-serves them).
    candidates: list[TurnCandidate] = []
    turns_by_game: dict[str, Turn] = {}
    for game in games:
        turn = (
            await db.execute(
                select(Turn)
                .where(Turn.game_id == game.id, Turn.resolved_at.is_(None))
                .order_by(Turn.round.desc(), Turn.turn.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if turn is None:
            continue
        player = players_by_game[game.id]
        submitted = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == player.id,
                )
            )
        ).scalar_one_or_none()
        if submitted is not None and not submitted.was_defaulted:
            continue
        candidates.append(
            TurnCandidate(
                game_id=game.id,
                round=turn.round,
                turn=turn.turn,
                deadline=_as_aware(turn.deadline_at),
            )
        )
        turns_by_game[game.id] = turn

    chosen = select_next_turn(candidates)
    if chosen is None:
        return NextTurnWaiting(
            reason="no_open_turns", next_poll_after_seconds=_POLL_WHEN_WAITING
        )

    game = next(g for g in games if g.id == chosen.game_id)
    turn = turns_by_game[game.id]
    player = players_by_game[game.id]

    all_players = (
        (await db.execute(select(Player).where(Player.game_id == game.id))).scalars().all()
    )
    latest_strategy = (
        await db.execute(
            select(StrategyPrompt)
            .where(StrategyPrompt.player_id == player.id)
            .order_by(StrategyPrompt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    module = get_game_module(game.game_type)
    static = TurnStatic(
        game_id=game.id,
        rules_version=game.rules_version,
        rules=module.rules_text(),
        total_rounds=game.total_rounds,
        turns_per_round=game.turns_per_round,
        your_agent_id=player.agent_id,
        all_agent_ids=sorted(p.agent_id for p in all_players),
        your_strategy=latest_strategy.prompt_text if latest_strategy else None,
    )
    # Same raw payload shape as the per-game /turn poll, so the loop and a direct
    # poll hand the bot identical data — just with the game_id attached.
    history = _group_into_turns(await _load_action_records(db, game))
    return NextTurnYourTurn(
        game_id=game.id,
        static=static,
        history=history,
        scoreboard=await _build_scoreboard(db, game),
        current=CurrentTurn(
            round=turn.round,
            turn=turn.turn,
            deadline=turn.deadline_at,
            turn_token=turn.turn_token,
        ),
        preferred_provider=bot.provider.value if bot.provider else None,
        preferred_model=bot.model,
    )
