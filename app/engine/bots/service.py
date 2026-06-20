"""DB-facing helpers for running bots inside live games."""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.bots.runtime import (
    build_bot_profile,
    choose_bot_action_decision,
    choose_bot_talk_decision,
)
from app.engine.bots.types import BotContext
from app.engine.player_move import record_player_action
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.match import Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.ops_events import log_ops_event
from app.read_models.matches import load_action_records, load_scoreboard
from app.schemas.agent import TalkMessage

logger = logging.getLogger(__name__)

Phase = Literal["talk", "act"]


async def auto_submit_bot_phase(
    db: AsyncSession,
    game: Match,
    turn: Turn,
    module: Any,
    *,
    phase: Phase,
) -> int:
    """Submit every server-driven move for the current phase.

    "Server-driven" means a seat the platform plays for: scripted **bots**, plus
    **human seats on autopilot** (a human who left a match in progress — their
    seat keeps playing Hoard so the table never waits on them). Both are submitted
    immediately, before the loop waits on live players.

    Returns the number of seats that successfully posted a talk message or action.
    """
    active_players = await _load_active_players_with_bots(db, game.id)
    if not active_players:
        return 0

    all_agent_ids = [player.seat_name for player, _ in active_players]
    # Bots reason in public seat names, but record_submission resolves a move's
    # target by the internal integer Player.agent_id. Translate seat name ->
    # agent_id before recording, exactly as the real-agent API path does
    # (app/routes/agent_api.py); without it a bot's first HELP/HURT crashes the
    # turn loop with `operator does not exist: integer = character varying`.
    agent_id_by_seat_name = {
        player.seat_name: player.agent_id for player, _ in active_players
    }
    bot_players = [
        (player, agent)
        for player, agent in active_players
        if agent.kind == AgentKind.BOT and agent.status == AgentStatus.ACTIVE
    ]
    autopilot_players = [
        player
        for player, agent in active_players
        if agent.kind == AgentKind.HUMAN and player.autopilot_at is not None
    ]
    if not bot_players and not autopilot_players:
        return 0

    history = await load_action_records(db, game.id)
    scoreboard = await load_scoreboard(db, game.id, active_only=True)
    current_talk_messages = (
        await _load_current_talk_messages(db, turn.id) if phase == "act" else []
    )

    posted = 0
    for player, agent in bot_players:
        try:
            profile = build_bot_profile(agent)
        except ValueError:
            # After creation-time validation this should never happen.  Log at
            # ERROR so it is visible in production monitoring if it does.
            log_ops_event(
                logger,
                logging.ERROR,
                "bot_profile_invalid",
                f"Skipping malformed bot {agent.id} — profile is invalid",
                agent_id=agent.id,
            )
            continue

        context = BotContext(
            game_id=game.id,  # internal bot DTO field; kept as game_id (see types.py)
            game_started_at=game.started_at or game.scheduled_start,
            round=turn.round,
            turn=turn.turn,
            phase=phase,
            your_agent_id=player.seat_name,
            all_agent_ids=all_agent_ids,
            history=history,
            scoreboard=scoreboard,
            current_talk_messages=current_talk_messages,
        )

        if phase == "talk":
            talk_decision = choose_bot_talk_decision(context, profile)
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
            action_decision = choose_bot_action_decision(context, profile)
            existing_submission = await _existing_submission(db, turn.id, player.id)
            await record_player_action(
                db,
                module,
                turn,
                player,
                move=action_decision.move,
                all_seat_names=all_agent_ids,
                agent_id_by_seat_name=agent_id_by_seat_name,
                existing=existing_submission,
            )
        posted += 1

    posted += await _auto_submit_autopilot(
        db, game, turn, module, autopilot_players, phase=phase
    )

    await db.commit()
    return posted


async def _auto_submit_autopilot(
    db: AsyncSession,
    game: Match,
    turn: Turn,
    module: Any,
    players: list[Player],
    *,
    phase: Phase,
) -> int:
    """Auto-submit for human seats that left mid-match (autopilot Hoard).

    A leaver never deliberates: in the talk phase they send nothing, and in the
    act phase they play the game's default move (Hoard for PD). Recorded
    immediately so a departed human never makes the table wait on the clock.
    """
    posted = 0
    for player in players:
        if phase == "talk":
            existing_message = await _existing_message(db, turn.id, player.id)
            if existing_message is not None and not existing_message.was_defaulted:
                continue
            await module.record_message(
                db, turn, player, "", "", existing=existing_message
            )
        else:
            existing_submission = await _existing_submission(db, turn.id, player.id)
            if existing_submission is not None and not existing_submission.was_defaulted:
                continue
            move = await module.default_move(db, game, player)
            await module.record_submission(
                db, turn, player, move, existing=existing_submission
            )
        posted += 1
    return posted


async def _load_active_players_with_bots(
    db: AsyncSession, match_id: str
) -> list[tuple[Player, Agent]]:
    rows = (
        (
            await db.execute(
                select(Player, Agent)
                .join(Agent, Agent.id == Player.agent_id)
                .where(
                    Player.match_id == match_id,
                    Player.left_at.is_(None),
                )
                .order_by(Player.seat_name)
            )
        )
        .all()
    )
    return [(player, agent) for player, agent in rows]


async def _load_current_talk_messages(
    db: AsyncSession, turn_id: int
) -> list[TalkMessage]:
    rows = (
        (
            await db.execute(
                select(TurnMessage, Player.seat_name)
                .join(Player, Player.id == TurnMessage.player_id)
                .where(TurnMessage.turn_id == turn_id, TurnMessage.was_defaulted.is_(False))
                .order_by(Player.seat_name)
            )
        )
        .all()
    )
    return [TalkMessage(agent_id=seat_name, message=msg.text) for msg, seat_name in rows]


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
