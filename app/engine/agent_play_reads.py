"""DB-to-payload projection helpers for the agent-play service.

These functions read match/turn/player rows and project them into the public
schema shapes (scoreboards, standings, talk messages, current-turn payloads,
action history). They sit above ``agent_play_guards`` and below both the
per-match verbs and the connection-level next-turn fan-out, so any helper used
by both of those siblings lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence, cast

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.agent_play_guards import _as_aware, _err, _seat_name_map
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.schemas.agent import (
    Action,
    CurrentTurn,
    HistoryAction,
    HistoryTurn,
    ScoreboardRow,
    StandingRow,
    TalkMessage,
)


@dataclass(frozen=True)
class _PublicActionRecord:
    round: int
    turn: int
    actor_id: str
    action: Action
    target_id: str | None
    message: str
    points_delta: int
    was_defaulted: bool


async def _load_public_action_records(
    db: AsyncSession,
    match_id: str,
    players: Sequence[Player],
) -> list[_PublicActionRecord]:
    seat_name_by_agent_id = _seat_name_map(players)
    seat_name_by_player_id = {player.id: player.seat_name for player in players}
    public_actions: list[_PublicActionRecord] = []
    turns = (
        (
            await db.execute(
                select(Turn).where(
                    Turn.match_id == match_id, Turn.resolved_at.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for turn in sorted(turns, key=lambda t: (t.round, t.turn)):
        message_rows = (
            (
                await db.execute(
                    select(TurnMessage, Player.id)
                    .join(Player, Player.id == TurnMessage.player_id)
                    .where(TurnMessage.turn_id == turn.id)
                )
            )
            .all()
        )
        message_by_player_id = {player_id: msg.text for msg, player_id in message_rows}
        submission_rows = (
            (
                await db.execute(
                    select(TurnSubmission, Player.id, Player.agent_id)
                    .join(Player, Player.id == TurnSubmission.player_id)
                    .where(TurnSubmission.turn_id == turn.id)
                )
            )
            .all()
        )
        for submission, player_id, agent_id in submission_rows:
            public_actions.append(
                _PublicActionRecord(
                    round=turn.round,
                    turn=turn.turn,
                    actor_id=seat_name_by_agent_id.get(agent_id, str(agent_id)),
                    action=cast(Action, submission.action),
                    target_id=(
                        seat_name_by_player_id.get(submission.target_player_id)
                        if submission.target_player_id is not None
                        else None
                    ),
                    message=message_by_player_id.get(player_id, submission.message),
                    points_delta=submission.points_delta,
                    was_defaulted=submission.was_defaulted,
                )
            )
    return public_actions


def _public_scoreboard(players: Sequence[Player]) -> list[ScoreboardRow]:
    ordered = sorted(players, key=lambda player: (-player.current_round_score, player.seat_name))
    return [
        ScoreboardRow(
            agent_id=player.seat_name,
            round_score=player.current_round_score,
            round_wins=player.total_round_wins,
        )
        for player in ordered
    ]


def _public_standings(players: Sequence[Player]) -> list[StandingRow]:
    ordered = sorted(players, key=lambda player: (-player.current_round_score, player.seat_name))
    return [
        StandingRow(
            agent_id=player.seat_name,
            round_score=player.current_round_score,
            rank=index + 1,
        )
        for index, player in enumerate(ordered)
    ]


async def _load_talk_messages(db: AsyncSession, turn: Turn) -> list[TalkMessage]:
    if turn.phase != "act":
        return []
    rows = (
        (
            await db.execute(
                select(TurnMessage, Player.seat_name)
                .join(Player, Player.id == TurnMessage.player_id)
                .where(TurnMessage.turn_id == turn.id)
                .order_by(Player.seat_name)
            )
        )
        .all()
    )
    return [TalkMessage(agent_id=seat_name, message=msg.text) for msg, seat_name in rows]


async def _build_current_turn(db: AsyncSession, turn: Turn) -> CurrentTurn:
    return CurrentTurn(
        round=turn.round,
        turn=turn.turn,
        deadline=turn.deadline_at,
        turn_token=turn.turn_token,
        phase=cast(Literal["talk", "act"], turn.phase),
        talk_messages=await _load_talk_messages(db, turn),
    )


async def _load_active_phase_turn(
    db: AsyncSession,
    match_id: str,
    turn_token: str,
    expected_phase: Literal["talk", "act"],
) -> tuple[Match, Turn]:
    game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
    if game.state != GameState.ACTIVE:
        raise _err(
            "GAME_NOT_ACTIVE",
            "Match is not active.",
            status.HTTP_409_CONFLICT,
        )

    turn = (
        await db.execute(
            select(Turn).where(Turn.match_id == game.id, Turn.turn_token == turn_token)
        )
    ).scalar_one_or_none()
    if turn is None:
        raise _err(
            "STALE_TURN_TOKEN",
            "turn_token doesn't match the open turn.",
            status.HTTP_409_CONFLICT,
        )
    if turn.resolved_at is not None:
        raise _err(
            "STALE_TURN_TOKEN",
            "Turn already resolved.",
            status.HTTP_409_CONFLICT,
        )
    if turn.phase != expected_phase:
        raise _err(
            "WRONG_PHASE",
            f"Turn is not in {expected_phase} phase.",
            status.HTTP_409_CONFLICT,
        )
    if datetime.now(timezone.utc) >= _as_aware(turn.deadline_at):
        raise _err("DEADLINE_PASSED", "Submission past deadline.", status.HTTP_410_GONE)
    return game, turn


async def _existing_message_for_player(
    db: AsyncSession, turn: Turn, player: Player
) -> TurnMessage | None:
    return (
        await db.execute(
            select(TurnMessage).where(
                TurnMessage.turn_id == turn.id,
                TurnMessage.player_id == player.id,
            )
        )
    ).scalar_one_or_none()


async def _existing_submission_for_player(
    db: AsyncSession, turn: Turn, player: Player
) -> TurnSubmission | None:
    return (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id,
                TurnSubmission.player_id == player.id,
            )
        )
    ).scalar_one_or_none()


def _group_into_turns(actions: Sequence[_PublicActionRecord]) -> list[HistoryTurn]:
    by_rt: dict[tuple[int, int], list[HistoryAction]] = {}
    for action in sorted(actions, key=lambda x: (x.round, x.turn)):
        by_rt.setdefault((action.round, action.turn), []).append(
            HistoryAction(
                agent_id=action.actor_id,
                action=action.action,
                target_id=action.target_id,
                message=action.message,
                points_delta=action.points_delta,
            )
        )
    return [
        HistoryTurn(round=round_no, turn=turn_no, actions=acts)
        for (round_no, turn_no), acts in sorted(by_rt.items())
    ]


def _parse_cursor(since: str | None) -> tuple[int, int] | None:
    if not since:
        return None
    parts = since.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise _err("INVALID_CURSOR", "since must be 'round.turn'.", status.HTTP_400_BAD_REQUEST)
    return int(parts[0]), int(parts[1])
