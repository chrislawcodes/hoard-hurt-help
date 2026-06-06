"""Read models for match-facing pages, APIs, and engines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.schemas.agent import ScoreboardRow


@dataclass(frozen=True)
class ResolvedTurnRows:
    """Resolved turn rows grouped for read-side projections."""

    players: list[Player]
    players_by_id: dict[int, Player]
    turns: list[Turn]
    messages_by_turn: dict[int, list[TurnMessage]]
    submissions_by_turn: dict[int, list[TurnSubmission]]


@dataclass(frozen=True)
class TimelineMessage:
    """A public talk message with DB ids resolved to agent ids."""

    agent_id: str
    text: str
    thinking: str
    was_defaulted: bool
    submitted_at: datetime | None


@dataclass(frozen=True)
class TimelineAction:
    """A submitted action with actor/target DB ids resolved to agent ids."""

    agent_id: str
    action: str
    target_id: str | None
    message: str
    thinking: str
    points_delta: int
    round_score_after: int
    submitted_at: datetime | None
    was_defaulted: bool


@dataclass(frozen=True)
class TimelineTurn:
    """One match turn as a DB-free read model."""

    round: int
    turn: int
    messages: list[TimelineMessage]
    actions: list[TimelineAction]


async def count_players(
    db: AsyncSession,
    match_id: str,
    *,
    active_only: bool = False,
) -> int:
    """Count seated players, optionally excluding players who left."""

    stmt = select(func.count()).select_from(Player).where(Player.match_id == match_id)
    if active_only:
        stmt = stmt.where(Player.left_at.is_(None))
    return int(await db.scalar(stmt) or 0)


async def load_players(
    db: AsyncSession,
    match_id: str,
    *,
    active_only: bool = False,
) -> list[Player]:
    """Load players in stable seat-name order."""

    stmt = select(Player).where(Player.match_id == match_id).order_by(Player.seat_name)
    if active_only:
        stmt = stmt.where(Player.left_at.is_(None))
    return list((await db.execute(stmt)).scalars().all())


async def load_scoreboard(
    db: AsyncSession,
    match_id: str,
    *,
    active_only: bool = False,
) -> list[ScoreboardRow]:
    """Current scoreboard rows for a match."""

    return [
        ScoreboardRow(
            agent_id=p.seat_name,
            round_score=p.current_round_score,
            round_wins=p.total_round_wins,
        )
        for p in await load_players(db, match_id, active_only=active_only)
    ]


async def load_player_records(
    db: AsyncSession,
    match_id: str,
    *,
    active_only: bool = True,
) -> list[PlayerRecord]:
    """DB-free player records for pure analysis/summary engines."""

    return [
        PlayerRecord(
            agent_id=p.seat_name,
            round_score=p.current_round_score,
            total_score=p.total_round_score,
            round_wins=p.total_round_wins,
        )
        for p in await load_players(db, match_id, active_only=active_only)
    ]


async def _load_turn_rows(
    db: AsyncSession,
    match_id: str,
    *,
    resolved_only: bool,
) -> ResolvedTurnRows:
    """Load players plus turns with grouped messages and submissions."""

    players = await load_players(db, match_id)
    players_by_id = {p.id: p for p in players}
    turn_stmt = select(Turn).where(Turn.match_id == match_id).order_by(Turn.round, Turn.turn)
    if resolved_only:
        turn_stmt = turn_stmt.where(Turn.resolved_at.is_not(None))
    turns = list((await db.execute(turn_stmt)).scalars().all())
    turn_ids = [t.id for t in turns]
    messages_by_turn: dict[int, list[TurnMessage]] = {}
    submissions_by_turn: dict[int, list[TurnSubmission]] = {}
    if not turn_ids:
        return ResolvedTurnRows(
            players=players,
            players_by_id=players_by_id,
            turns=turns,
            messages_by_turn=messages_by_turn,
            submissions_by_turn=submissions_by_turn,
        )

    messages = list(
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

    submissions = list(
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
    for submission in submissions:
        submissions_by_turn.setdefault(submission.turn_id, []).append(submission)

    return ResolvedTurnRows(
        players=players,
        players_by_id=players_by_id,
        turns=turns,
        messages_by_turn=messages_by_turn,
        submissions_by_turn=submissions_by_turn,
    )


async def load_resolved_turn_rows(db: AsyncSession, match_id: str) -> ResolvedTurnRows:
    """Load players plus resolved turns with grouped messages and submissions."""

    return await _load_turn_rows(db, match_id, resolved_only=True)


async def load_match_timeline(
    db: AsyncSession,
    match_id: str,
    *,
    resolved_only: bool = True,
) -> list[TimelineTurn]:
    """Load turn history with player ids resolved for viewers, exports, and APIs."""

    rows = await _load_turn_rows(db, match_id, resolved_only=resolved_only)
    timeline: list[TimelineTurn] = []
    for turn in rows.turns:
        turn_messages = rows.messages_by_turn.get(turn.id, [])
        submissions = rows.submissions_by_turn.get(turn.id, [])
        messages: list[TimelineMessage]
        if turn_messages:
            messages = [
                TimelineMessage(
                    agent_id=rows.players_by_id[message.player_id].seat_name,
                    text=message.text,
                    thinking=message.thinking,
                    was_defaulted=message.was_defaulted,
                    submitted_at=message.submitted_at,
                )
                for message in turn_messages
                if message.player_id in rows.players_by_id
            ]
        else:
            messages = [
                TimelineMessage(
                    agent_id=rows.players_by_id[submission.player_id].seat_name,
                    text=submission.message,
                    thinking="",
                    was_defaulted=submission.was_defaulted,
                    submitted_at=submission.submitted_at,
                )
                for submission in submissions
                if submission.player_id in rows.players_by_id
            ]

        actions: list[TimelineAction] = []
        for submission in submissions:
            actor = rows.players_by_id.get(submission.player_id)
            target = (
                rows.players_by_id.get(submission.target_player_id)
                if submission.target_player_id
                else None
            )
            if actor is None:
                continue
            actions.append(
                TimelineAction(
                    agent_id=actor.seat_name,
                    action=submission.action,
                    target_id=target.seat_name if target else None,
                    message=submission.message,
                    thinking=submission.thinking,
                    points_delta=submission.points_delta,
                    round_score_after=submission.round_score_after,
                    submitted_at=submission.submitted_at,
                    was_defaulted=submission.was_defaulted,
                )
            )
        timeline.append(
            TimelineTurn(
                round=turn.round,
                turn=turn.turn,
                messages=messages,
                actions=actions,
            )
        )
    return timeline


async def load_action_records(db: AsyncSession, match_id: str) -> list[ActionRecord]:
    """Every resolved submission as DB-free records with DB ids resolved to agent ids."""

    records: list[ActionRecord] = []
    for turn in await load_match_timeline(db, match_id):
        message_by_agent = {message.agent_id: message.text for message in turn.messages}
        for action in turn.actions:
            records.append(
                ActionRecord(
                    round=turn.round,
                    turn=turn.turn,
                    actor_id=action.agent_id,
                    action=cast(Action, action.action),
                    target_id=action.target_id,
                    message=message_by_agent.get(action.agent_id, action.message),
                    points_delta=action.points_delta,
                    round_score_after=action.round_score_after,
                    was_defaulted=action.was_defaulted,
                )
            )
    return records
