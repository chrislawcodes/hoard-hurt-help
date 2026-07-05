"""Read models for match-facing pages, APIs, and engines."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match
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
    quantity: int | None
    face: int | None
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


async def _seat_in_active_match(
    db: AsyncSession,
    seat_filter: ColumnElement[bool],
) -> bool:
    """True if any seat matching *seat_filter* sits in an ACTIVE match.

    The one active-match predicate: seat not left (``left_at IS NULL``), match
    ACTIVE (rated OR practice), existence-checked with ``LIMIT 1``. Callers pick
    which seat column to filter on.
    """
    row = (
        await db.execute(
            select(Player.id)
            .join(Match, Match.id == Player.match_id)
            .where(
                seat_filter,
                Player.left_at.is_(None),
                Match.state == GameState.ACTIVE,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def agent_has_active_match(db: AsyncSession, agent_id: int) -> bool:
    """True if any seat of this agent is in an active match (rated OR practice)."""
    return await _seat_in_active_match(db, Player.agent_id == agent_id)


async def version_has_active_match(db: AsyncSession, version_id: int) -> bool:
    """True if this version is seated in any active match (rated OR practice)."""
    return await _seat_in_active_match(db, Player.agent_version_id == version_id)


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


async def count_players_by_match(
    db: AsyncSession,
    match_ids: Sequence[str],
    *,
    active_only: bool = False,
) -> dict[str, int]:
    """Seated-player counts for many matches in a single grouped query.

    Returns a {match_id: count} map. Matches with no rows are absent from the
    map; callers should treat a missing id as 0. This replaces calling
    count_players() once per match (an N+1 query) when rendering list pages.
    """

    if not match_ids:
        return {}
    stmt = (
        select(Player.match_id, func.count())
        .where(Player.match_id.in_(match_ids))
        .group_by(Player.match_id)
    )
    if active_only:
        stmt = stmt.where(Player.left_at.is_(None))
    rows = (await db.execute(stmt)).all()
    return {match_id: int(count) for match_id, count in rows}


async def _agent_count(db: AsyncSession, match_id: str) -> int:
    """Count non-SIM (real agent) players for a match."""
    result = await db.scalar(
        select(func.count())
        .select_from(Player)
        .join(Agent, Agent.id == Player.agent_id)
        .where(Player.match_id == match_id, Agent.kind != AgentKind.BOT)
    )
    return int(result or 0)


async def _agent_counts(db: AsyncSession, match_ids: Sequence[str]) -> dict[str, int]:
    """Non-SIM (real agent) player counts for many matches in one grouped query.

    Returns a {match_id: count} map; matches with no real agents are absent and
    should be read as 0. Batched form of _agent_count to avoid an N+1 query when
    rendering lists of finished matches.
    """
    if not match_ids:
        return {}
    rows = (
        await db.execute(
            select(Player.match_id, func.count())
            .select_from(Player)
            .join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id.in_(match_ids), Agent.kind != AgentKind.BOT)
            .group_by(Player.match_id)
        )
    ).all()
    return {match_id: int(count) for match_id, count in rows}


async def _upcoming_views(db: AsyncSession) -> list[dict]:
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
    # Active-player counts for every upcoming game in one grouped query (matches
    # _player_count's active_only filter), instead of a query per game.
    player_counts = await count_players_by_match(db, [g.id for g in games], active_only=True)
    views: list[dict] = []
    for g in games:
        views.append(
            {
                "id": g.id,
                "game_type": g.game,
                "name": g.name,
                "match_kind": g.match_kind,
                "scheduled_start": g.scheduled_start,
                "max_players": g.max_players,
                "player_count": player_counts.get(g.id, 0),
            }
        )
    return views


async def winner_agent_id_by_player(
    db: AsyncSession,
    player_ids: Sequence[int],
) -> dict[int, int]:
    """Map each winning player's id to its agent id in a single query.

    Returns a {player_id: agent_id} map for the given player ids, so a list
    page can resolve every match winner at once instead of one lookup per match.
    """

    if not player_ids:
        return {}
    rows = (
        await db.execute(select(Player.id, Player.agent_id).where(Player.id.in_(player_ids)))
    ).all()
    return {player_id: agent_id for player_id, agent_id in rows}


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


def rank_standings(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rank standings rows by round-wins then round-score, numbering from 1.

    Reads only ``round_wins``/``round_score`` for the (stable) sort and copies
    ``agent_id`` through into a fresh ranked-core row. Callers own the load — and
    thus the tie-break order among rows with equal wins and score — plus any
    per-row decoration they layer on top of the returned core.
    """
    ranked = sorted(rows, key=lambda r: (-r["round_wins"], -r["round_score"]))
    if limit is not None:
        ranked = ranked[:limit]
    return [
        {
            "agent_id": r["agent_id"],
            "round_score": r["round_score"],
            "round_wins": r["round_wins"],
            "rank": i,
        }
        for i, r in enumerate(ranked, start=1)
    ]


def rank_standings_by_match(
    rows_by_match: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Batched :func:`rank_standings`: rank each match's rows, keeping every key."""
    return {mid: rank_standings(rows, limit=limit) for mid, rows in rows_by_match.items()}


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
                    quantity=submission.quantity,
                    face=submission.face,
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
