"""Read models for match-facing pages, APIs, and engines."""

from __future__ import annotations

from dataclasses import dataclass
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
    """Load players in stable agent-id order."""

    stmt = select(Player).where(Player.match_id == match_id).order_by(Player.agent_id)
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
            agent_id=p.agent_id,
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
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            total_score=p.total_round_score,
            round_wins=p.total_round_wins,
        )
        for p in await load_players(db, match_id, active_only=active_only)
    ]


async def load_resolved_turn_rows(db: AsyncSession, match_id: str) -> ResolvedTurnRows:
    """Load players plus resolved turns with grouped messages and submissions."""

    players = await load_players(db, match_id)
    players_by_id = {p.id: p for p in players}
    turns = list(
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


async def load_action_records(db: AsyncSession, match_id: str) -> list[ActionRecord]:
    """Every resolved submission as DB-free records with DB ids resolved to agent ids."""

    rows = await load_resolved_turn_rows(db, match_id)
    name_by_id = {p.id: p.agent_id for p in rows.players}
    message_by_key = {
        (message.turn_id, message.player_id): message.text
        for messages in rows.messages_by_turn.values()
        for message in messages
    }
    records: list[ActionRecord] = []
    for turn in rows.turns:
        for submission in rows.submissions_by_turn.get(turn.id, []):
            actor_id = name_by_id.get(submission.player_id)
            if actor_id is None:
                continue
            target_id = (
                name_by_id.get(submission.target_player_id)
                if submission.target_player_id
                else None
            )
            records.append(
                ActionRecord(
                    round=turn.round,
                    turn=turn.turn,
                    actor_id=actor_id,
                    action=cast(Action, submission.action),
                    target_id=target_id,
                    message=message_by_key.get(
                        (submission.turn_id, submission.player_id),
                        submission.message,
                    ),
                    points_delta=submission.points_delta,
                    round_score_after=submission.round_score_after,
                    was_defaulted=submission.was_defaulted,
                )
            )
    return records
