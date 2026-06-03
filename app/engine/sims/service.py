"""DB-facing helpers for running Sims inside live games."""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.game_records import Action, ActionRecord
from app.engine.sims.runtime import (
    build_sim_profile,
    choose_action_decision,
    choose_talk_decision,
)
from app.engine.sims.types import SimContext
from app.models.bot import Bot, BotKind, BotStatus
from app.models.match import Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.schemas.agent import ScoreboardRow, TalkMessage

logger = logging.getLogger(__name__)

Phase = Literal["talk", "act"]


async def auto_submit_sim_phase(
    db: AsyncSession,
    game: Match,
    turn: Turn,
    module: Any,
    *,
    phase: Phase,
) -> int:
    """Submit every eligible Sim move for the current phase.

    Returns the number of Sims that successfully posted a talk message or action.
    """
    active_players = await _load_active_players_with_bots(db, game.id)
    if not active_players:
        return 0

    all_agent_ids = [player.agent_id for player, _ in active_players]
    sim_players = [
        (player, bot)
        for player, bot in active_players
        if bot.kind == BotKind.SIM and bot.status == BotStatus.ACTIVE
    ]
    if not sim_players:
        return 0

    history = await _load_action_records(db, game.id)
    scoreboard = await _load_scoreboard(db, game.id)
    current_talk_messages = (
        await _load_current_talk_messages(db, turn.id) if phase == "act" else []
    )

    posted = 0
    for player, bot in sim_players:
        try:
            profile = build_sim_profile(bot)
        except ValueError:
            logger.warning("Skipping malformed Sim bot %s", bot.id)
            continue

        context = SimContext(
            game_id=game.id,  # internal Sim DTO field; kept as game_id (see types.py)
            round=turn.round,
            turn=turn.turn,
            phase=phase,
            your_agent_id=player.agent_id,
            all_agent_ids=all_agent_ids,
            history=history,
            scoreboard=scoreboard,
            current_talk_messages=current_talk_messages,
        )

        if phase == "talk":
            talk_decision = choose_talk_decision(context, profile)
            existing_message = await _existing_message(db, turn.id, player.id)
            await module.record_message(
                db,
                turn,
                player,
                talk_decision.message,
                talk_decision.thinking,
                existing=existing_message,
            )
        else:
            action_decision = choose_action_decision(context, profile)
            move = action_decision.move
            module.validate_move(
                move, your_agent_id=player.agent_id, all_agent_ids=all_agent_ids
            )
            existing_submission = await _existing_submission(db, turn.id, player.id)
            await module.record_submission(
                db,
                turn,
                player,
                move,
                existing=existing_submission,
            )
        posted += 1

    await db.commit()
    return posted


async def _load_active_players_with_bots(
    db: AsyncSession, match_id: str
) -> list[tuple[Player, Bot]]:
    rows = (
        (
            await db.execute(
                select(Player, Bot)
                .join(Bot, Bot.id == Player.bot_id)
                .where(
                    Player.match_id == match_id,
                    Player.left_at.is_(None),
                )
                .order_by(Player.agent_id)
            )
        )
        .all()
    )
    return [(player, bot) for player, bot in rows]


async def _load_action_records(db: AsyncSession, match_id: str) -> list[ActionRecord]:
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
    if not turns:
        return []
    turn_by_id = {t.id: t for t in turns}
    name_by_id = {
        p.id: p.agent_id
        for p in (
            await db.execute(select(Player).where(Player.match_id == match_id))
        )
        .scalars()
        .all()
    }
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
    messages = (
        (
            await db.execute(
                select(TurnMessage).where(TurnMessage.turn_id.in_([t.id for t in turns]))
            )
        )
        .scalars()
        .all()
    )
    message_by_key = {(m.turn_id, m.player_id): m.text for m in messages}
    records: list[ActionRecord] = []
    for s in subs:
        t = turn_by_id[s.turn_id]
        target = name_by_id.get(s.target_player_id) if s.target_player_id else None
        records.append(
            ActionRecord(
                round=t.round,
                turn=t.turn,
                actor_id=name_by_id[s.player_id],
                action=cast(Action, s.action),
                target_id=target,
                message=message_by_key.get((s.turn_id, s.player_id), s.message),
                points_delta=s.points_delta,
                round_score_after=s.round_score_after,
                was_defaulted=s.was_defaulted,
            )
        )
    return records


async def _load_scoreboard(db: AsyncSession, match_id: str) -> list[ScoreboardRow]:
    players = (
        (
            await db.execute(
                select(Player).where(Player.match_id == match_id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return [
        ScoreboardRow(
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            round_wins=p.total_round_wins,
        )
        for p in players
    ]


async def _load_current_talk_messages(
    db: AsyncSession, turn_id: int
) -> list[TalkMessage]:
    rows = (
        (
            await db.execute(
                select(TurnMessage, Player.agent_id)
                .join(Player, Player.id == TurnMessage.player_id)
                .where(TurnMessage.turn_id == turn_id, TurnMessage.was_defaulted.is_(False))
                .order_by(Player.agent_id)
            )
        )
        .all()
    )
    return [TalkMessage(agent_id=agent_id, message=msg.text) for msg, agent_id in rows]


async def _existing_message(
    db: AsyncSession, turn_id: int, player_id: int
) -> TurnMessage | None:
    return (
        await db.execute(
            select(TurnMessage).where(
                TurnMessage.turn_id == turn_id,
                TurnMessage.player_id == player_id,
            )
        )
    ).scalar_one_or_none()


async def _existing_submission(
    db: AsyncSession, turn_id: int, player_id: int
) -> TurnSubmission | None:
    return (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn_id,
                TurnSubmission.player_id == player_id,
            )
        )
    ).scalar_one_or_none()
