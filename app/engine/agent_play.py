"""Shared agent-play service logic.

This module holds the HTTP agent API's core business logic so the FastAPI
routes can stay thin adapters and the MCP layer can reuse the same turn payload
and submission behavior.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence, cast

from fastapi import HTTPException, status
from sqlalchemy import false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import app.db as db_module
from app.deps import _parse_agent_turn_token
from app.engine.connection_activity import increment_turns_played, mark_first_move
from app.engine.next_turn import TurnCandidate, select_next_turn
from app.engine.turn_routing import (
    ConnectionRouteState,
    TurnPin,
    can_connection_claim_turn,
    connection_is_dead,
)
from app.games import get as get_game_module
from app.games.base import GameError
from app.identity import word_filter
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.models.user import User
from app.ops_events import log_ops_event
from app.schemas.agent import (
    Action,
    AgentStateResponse,
    ChatLine,
    ChatTranscriptResponse,
    CurrentTurn,
    FullStandingsResponse,
    HistoryAction,
    HistoryTurn,
    LeaveResponse,
    MessageResponse,
    OpponentHistoryResponse,
    ScoreboardRow,
    StandingRow,
    SubmitResponse,
    TalkMessage,
    TurnDetailResponse,
    TurnStatic,
    WaitingResponse,
    YourTurnResponse,
)

logger = logging.getLogger(__name__)

_MIN_POLL_INTERVAL = 1.0
_POLL_FAR_FROM_START = 30
_POLL_NEAR_START = 5
_POLL_WHEN_ACTIVE = 5
_NEAR_START_WINDOW_SECONDS = 180
_PULL_MIN_INTERVAL = 1.0
_LONG_POLL_HOLD_SECONDS = 0.0
_LONG_POLL_INTERVAL_SECONDS = 1.0

PollRateState = dict[int, float]
PullRateState = dict[tuple[int, str], float]


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


def _err(code: str, message: str, http: int, details: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=http,
        detail={"error": {"code": code, "message": message, "details": details or {}}},
    )


def _as_aware(dt: datetime) -> datetime:
    """SQLite drops timezone info on read; normalize to UTC-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _seat_name_map(players: Sequence[Player]) -> dict[int, str]:
    return {player.agent_id: player.seat_name for player in players}


def _check_poll_rate_limit(rate_state: PollRateState, agent_id: int) -> None:
    now_t = time.monotonic()
    last = rate_state.get(agent_id, 0.0)
    if now_t - last < _MIN_POLL_INTERVAL:
        raise _err("RATE_LIMITED", "Polling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
    rate_state[agent_id] = now_t


def _check_pull_rate_limit(rate_state: PullRateState, agent_id: int, bucket: str) -> None:
    now_t = time.monotonic()
    last = rate_state.get((agent_id, bucket), 0.0)
    if now_t - last < _PULL_MIN_INTERVAL:
        raise _err("RATE_LIMITED", "Pulling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
    rate_state[(agent_id, bucket)] = now_t


def _next_poll_before_start(game: Match) -> int:
    seconds_until_start = (
        _as_aware(game.scheduled_start) - datetime.now(timezone.utc)
    ).total_seconds()
    if seconds_until_start > _NEAR_START_WINDOW_SECONDS:
        return _POLL_FAR_FROM_START
    return _POLL_NEAR_START


def _validate_agent_turn_binding(
    agent_turn_token: str, *, turn_token: str, match_id: str, agent_id: int
) -> None:
    token_turn_token, token_agent_id, token_match_id = _parse_agent_turn_token(
        agent_turn_token
    )
    if (
        token_turn_token != turn_token
        or token_agent_id != agent_id
        or token_match_id != match_id
    ):
        raise _err(
            "STALE_TURN_TOKEN",
            "agent_turn_token doesn't match this agent and turn.",
            status.HTTP_409_CONFLICT,
        )


def _validate_agent_match_binding(
    agent_turn_token: str, *, match_id: str, agent_id: int
) -> None:
    _, token_agent_id, token_match_id = _parse_agent_turn_token(agent_turn_token)
    if token_agent_id != agent_id or token_match_id != match_id:
        raise _err(
            "STALE_TURN_TOKEN",
            "agent_turn_token doesn't match this agent and match.",
            status.HTTP_409_CONFLICT,
        )


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


async def _game_for(player: Player, match_id: str, db: AsyncSession) -> Match:
    return (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()


async def poll_turn(
    db: AsyncSession,
    *,
    match_id: str,
    player: Player,
    rate_state: PollRateState,
) -> WaitingResponse | YourTurnResponse:
    _check_poll_rate_limit(rate_state, player.agent_id)

    game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()

    if game.state in (GameState.SCHEDULED, GameState.REGISTERING):
        return WaitingResponse(
            reason="game_not_started",
            game_state=game.state.value,
            next_poll_after_seconds=_next_poll_before_start(game),
        )
    if game.state in (GameState.COMPLETED, GameState.CANCELLED):
        return WaitingResponse(
            reason="game_over",
            game_state=game.state.value,
            current_round=game.current_round,
            current_turn=game.current_turn,
        )

    turn = (
        await db.execute(
            select(Turn)
            .where(Turn.match_id == game.id, Turn.resolved_at.is_(None))
            .order_by(Turn.round.desc(), Turn.turn.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if turn is None:
        return WaitingResponse(
            reason="turn_not_open",
            game_state=game.state.value,
            current_round=game.current_round,
            current_turn=game.current_turn,
            next_poll_after_seconds=_POLL_WHEN_ACTIVE,
        )

    existing_sub = (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id,
                TurnSubmission.player_id == player.id,
            )
        )
    ).scalar_one_or_none()
    if existing_sub is not None and not existing_sub.was_defaulted:
        return WaitingResponse(
            reason="already_submitted",
            game_state=game.state.value,
            current_round=turn.round,
            current_turn=turn.turn,
            next_poll_after_seconds=_POLL_WHEN_ACTIVE,
        )

    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    seat_name_by_agent_id = _seat_name_map(all_players)
    current_version = None
    if player.agent_version_id is not None:
        current_version = (
            await db.execute(
                select(AgentVersion).where(AgentVersion.id == player.agent_version_id)
            )
        ).scalar_one_or_none()
    module = get_game_module(game.game)
    all_agent_ids = sorted(seat_name_by_agent_id.values())
    static = TurnStatic(
        match_id=game.id,
        rules_version=game.rules_version,
        rules=module.rules_text(game.total_rounds, game.turns_per_round),
        base_prompt=module.agent_base_prompt(
            your_agent_id=player.seat_name,
            all_agent_ids=all_agent_ids,
            total_rounds=game.total_rounds,
            turns_per_round=game.turns_per_round,
        ),
        total_rounds=game.total_rounds,
        turns_per_round=game.turns_per_round,
        your_agent_id=player.seat_name,
        all_agent_ids=all_agent_ids,
        your_strategy=current_version.strategy_text if current_version else None,
    )
    history = _group_into_turns(await _load_public_action_records(db, game.id, all_players))
    return YourTurnResponse(
        static=static,
        history=history,
        scoreboard=_public_scoreboard(all_players),
        current=await _build_current_turn(db, turn),
        your_private_state=(await module.private_state_for(db, game, player)) or None,
        public_state=(await module.public_state_for(db, game, player)) or None,
    )


async def submit_talk(
    db: AsyncSession,
    *,
    match_id: str,
    player: Player,
    agent_turn_token: str,
    turn_token: str,
    message: str,
    thinking: str,
    is_connector_fallback: bool,
) -> MessageResponse:
    _validate_agent_turn_binding(
        agent_turn_token,
        turn_token=turn_token,
        match_id=match_id,
        agent_id=player.agent_id,
    )
    game, turn = await _load_active_phase_turn(db, match_id, turn_token, "talk")
    existing = await _existing_message_for_player(db, turn, player)
    if existing is not None and not existing.was_defaulted:
        return MessageResponse(
            received_at=existing.submitted_at or datetime.now(timezone.utc),
            phase_resolves_at=turn.deadline_at,
        )

    module = get_game_module(game.game)
    await module.record_message(
        db,
        turn,
        player,
        word_filter.mask(message),
        word_filter.mask(thinking),
        existing=existing,
        is_connector_fallback=is_connector_fallback,
    )
    if is_connector_fallback:
        log_ops_event(
            logger,
            logging.WARNING,
            "connector_fallback_move",
            f"connector fallback message recorded for agent {player.seat_name}"
            f" in match {match_id} (round={turn.round} turn={turn.turn})",
            agent_id=player.seat_name,
            match_id=match_id,
            phase="talk",
            round=turn.round,
            turn=turn.turn,
        )
    await db.commit()

    return MessageResponse(
        received_at=datetime.now(timezone.utc),
        phase_resolves_at=turn.deadline_at,
    )


def _pack_move(
    *,
    action: str | None,
    target_id: str | None,
    message: str,
    thinking: str,
    move: dict[str, object] | None,
) -> dict[str, object]:
    """Pack a submit into the generic move dict the game module validates.

    A non-PD game sends a free-form `move`; PD bots send action/target_id. Either
    way message/thinking ride along. The platform never interprets the move.
    """
    if move is not None:
        return {**move, "message": message, "thinking": thinking}
    return {
        "action": action,
        "target_id": target_id,
        "message": message,
        "thinking": thinking,
    }


# Keys that a game's `validation_snapshot` may add for `validate_move` only.
# They are stripped before `record_submission` so they never persist.
_LD_VALIDATION_SNAPSHOT_KEYS = {
    "standing_bid",
    "dice_counts",
    "active_actor",
    "total_dice",
    "wild",
}


async def submit_action(
    db: AsyncSession,
    *,
    match_id: str,
    player: Player,
    connection: Connection,
    agent_turn_token: str,
    turn_token: str,
    action: str | None,
    target_id: str | None,
    message: str,
    thinking: str,
    is_connector_fallback: bool,
    move: dict[str, object] | None = None,
) -> SubmitResponse:
    _validate_agent_turn_binding(
        agent_turn_token,
        turn_token=turn_token,
        match_id=match_id,
        agent_id=player.agent_id,
    )
    game, turn = await _load_active_phase_turn(db, match_id, turn_token, "act")
    existing = await _existing_submission_for_player(db, turn, player)
    if existing is not None and not existing.was_defaulted:
        return SubmitResponse(
            received_at=existing.submitted_at or datetime.now(timezone.utc),
            turn_will_resolve_at=turn.deadline_at,
        )

    module = get_game_module(game.game)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    seat_name_by_agent_id = _seat_name_map(all_players)
    all_agent_ids = sorted(seat_name_by_agent_id.values())
    built_move = _pack_move(
        action=action,
        target_id=target_id,
        message=word_filter.mask(message),
        thinking=word_filter.mask(thinking),
        move=move,
    )
    snapshot = await module.validation_snapshot(db, game, player)
    if snapshot:
        built_move = {**built_move, **snapshot}
    try:
        module.validate_move(
            built_move, your_agent_id=player.seat_name, all_agent_ids=all_agent_ids
        )
    except GameError as exc:
        raise _err(
            exc.code, exc.message, status.HTTP_400_BAD_REQUEST, exc.details
        ) from exc
    internal_move: dict[str, object] = {
        key: value for key, value in built_move.items() if key not in _LD_VALIDATION_SNAPSHOT_KEYS
    }
    if move is None and target_id is not None:
        target_player = next(
            (candidate for candidate in all_players if candidate.seat_name == target_id),
            None,
        )
        internal_move["target_id"] = target_player.agent_id if target_player else None
    await module.record_submission(
        db,
        turn,
        player,
        internal_move,
        existing=existing,
        is_connector_fallback=is_connector_fallback,
    )
    if is_connector_fallback:
        log_ops_event(
            logger,
            logging.WARNING,
            "connector_fallback_move",
            f"connector fallback action recorded for agent {player.seat_name}"
            f" in match {match_id} (round={turn.round} turn={turn.turn})",
            agent_id=player.seat_name,
            match_id=match_id,
            phase="act",
            round=turn.round,
            turn=turn.turn,
        )
    await db.commit()

    if not is_connector_fallback:
        await increment_turns_played(db, connection.id)
    await mark_first_move(db, player.agent_id)

    return SubmitResponse(
        received_at=datetime.now(timezone.utc),
        turn_will_resolve_at=turn.deadline_at,
    )


async def get_agent_state(
    db: AsyncSession,
    *,
    match_id: str,
    player: Player,
) -> AgentStateResponse:
    game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
    open_turn = (
        await db.execute(
            select(Turn)
            .where(Turn.match_id == game.id, Turn.resolved_at.is_(None))
            .order_by(Turn.round.desc(), Turn.turn.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    you_submitted = False
    if open_turn is not None:
        submission = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == open_turn.id,
                    TurnSubmission.player_id == player.id,
                )
            )
        ).scalar_one_or_none()
        you_submitted = submission is not None and not submission.was_defaulted

    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    return AgentStateResponse(
        match_id=game.id,
        game_state=game.state.value,
        current_round=game.current_round,
        current_turn=game.current_turn,
        deadline=open_turn.deadline_at if open_turn else None,
        you_have_submitted_current_turn=you_submitted,
        scoreboard=_public_scoreboard(all_players),
        all_agent_ids=sorted(player.seat_name for player in all_players),
    )


async def leave_match(
    db: AsyncSession,
    *,
    match_id: str,
    agent_turn_token: str,
    player: Player,
) -> LeaveResponse:
    _validate_agent_match_binding(
        agent_turn_token,
        match_id=match_id,
        agent_id=player.agent_id,
    )
    game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
    if game.state == GameState.ACTIVE:
        raise _err(
            "GAME_ALREADY_STARTED",
            "Cannot leave a game that has already started.",
            status.HTTP_409_CONFLICT,
        )
    player.left_at = datetime.now(timezone.utc)
    await db.commit()
    return LeaveResponse(game_state=game.state.value, effective_at=player.left_at)


async def opponent_history(
    db: AsyncSession,
    *,
    match_id: str,
    opponent_id: str,
    player: Player,
    rate_state: PullRateState,
) -> OpponentHistoryResponse:
    _check_pull_rate_limit(rate_state, player.agent_id, "opponent_history")
    game = await _game_for(player, match_id, db)
    opponent = (
        await db.execute(
            select(Player).where(Player.match_id == game.id, Player.seat_name == opponent_id)
        )
    ).scalar_one_or_none()
    if opponent is None:
        raise _err(
            "INVALID_TARGET",
            "Opponent not in this game.",
            status.HTTP_400_BAD_REQUEST,
            details={"reason": "unknown_agent"},
        )
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    you = player.seat_name
    public_actions = await _load_public_action_records(db, game.id, all_players)
    actions = [
        action
        for action in public_actions
        if action.actor_id == opponent.seat_name
        or (action.actor_id == you and action.target_id == opponent.seat_name)
    ]
    return OpponentHistoryResponse(
        opponent_id=opponent.seat_name,
        turns=_group_into_turns(actions),
    )


async def chat_transcript(
    db: AsyncSession,
    *,
    match_id: str,
    player: Player,
    rate_state: PullRateState,
    since: str | None = None,
) -> ChatTranscriptResponse:
    _check_pull_rate_limit(rate_state, player.agent_id, "chat")
    game = await _game_for(player, match_id, db)
    cursor = _parse_cursor(since)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    lines: list[ChatLine] = []
    for action in sorted(
        await _load_public_action_records(db, game.id, all_players),
        key=lambda x: (x.round, x.turn),
    ):
        if action.was_defaulted or not action.message:
            continue
        if cursor is not None and (action.round, action.turn) <= cursor:
            continue
        lines.append(
            ChatLine(
                round=action.round,
                turn=action.turn,
                from_agent_id=action.actor_id,
                target_id=action.target_id,
                message=action.message,
            )
        )
    next_cursor = f"{lines[-1].round}.{lines[-1].turn}" if lines else since
    return ChatTranscriptResponse(since=since, messages=lines, next_cursor=next_cursor)


async def turn_detail(
    db: AsyncSession,
    *,
    match_id: str,
    round: int,
    turn: int,
    player: Player,
    rate_state: PullRateState,
) -> TurnDetailResponse:
    _check_pull_rate_limit(rate_state, player.agent_id, "turn_detail")
    game = await _game_for(player, match_id, db)
    turn_row = (
        await db.execute(
            select(Turn).where(
                Turn.match_id == game.id,
                Turn.round == round,
                Turn.turn == turn,
                Turn.resolved_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if turn_row is None:
        raise _err("NOT_FOUND", "No such resolved turn.", status.HTTP_404_NOT_FOUND)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    these = [
        action
        for action in await _load_public_action_records(db, game.id, all_players)
        if action.round == round and action.turn == turn
    ]
    grouped = _group_into_turns(these)
    actions = grouped[0].actions if grouped else []
    return TurnDetailResponse(round=round, turn=turn, actions=actions)


async def standings(
    db: AsyncSession,
    *,
    match_id: str,
    player: Player,
    rate_state: PullRateState,
) -> FullStandingsResponse:
    _check_pull_rate_limit(rate_state, player.agent_id, "standings")
    game = await _game_for(player, match_id, db)
    all_players = (
        (
            await db.execute(
                select(Player).where(Player.match_id == game.id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    rows = _public_standings(all_players)
    return FullStandingsResponse(rows=rows, total_players=len(rows))


async def _load_route_states(
    db: AsyncSession, connection: Connection
) -> tuple[dict[int, ConnectionRouteState], ConnectionRouteState]:
    conns = (
        (
            await db.execute(
                select(Connection).where(Connection.user_id == connection.user_id)
            )
        )
        .scalars()
        .all()
    )
    conn_ids = [conn.id for conn in conns]
    enabled_by_conn: dict[int, set[str]] = {}
    if conn_ids:
        cp_rows = (
            (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id.in_(conn_ids),
                        ConnectionProviderRow.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in cp_rows:
            enabled_by_conn.setdefault(row.connection_id, set()).add(row.provider.value)

    def _state(conn: Connection) -> ConnectionRouteState:
        return ConnectionRouteState(
            connection_id=conn.id,
            enabled_providers=frozenset(enabled_by_conn.get(conn.id, set())),
            paused=conn.status == ConnectionStatus.PAUSED,
            deleted=conn.deleted_at is not None,
            last_seen_at=conn.last_seen_at,
        )

    by_id = {conn.id: _state(conn) for conn in conns}
    polling = by_id.get(connection.id) or _state(connection)
    return by_id, polling


async def _collect_candidates(
    db: AsyncSession, connection: Connection, now: datetime
) -> tuple[list[TurnCandidate], dict[str, object]]:
    connections_by_id, polling_state = await _load_route_states(db, connection)

    agent_rows = (
        await db.execute(
            select(Agent, Player, Match, AgentVersion)
            .join(Player, Player.agent_id == Agent.id)
            .join(Match, Match.id == Player.match_id)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(
                Agent.user_id == connection.user_id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
                Player.left_at.is_(None),
                Match.state == GameState.ACTIVE,
            )
        )
    ).all()

    latest_turn_by_match: dict[str, Turn] = {}
    player_by_key: dict[tuple[int, str], Player] = {}
    agent_by_id: dict[int, Agent] = {}
    version_by_agent_id: dict[int, AgentVersion] = {}
    dead_ids = [
        cid
        for cid, state in connections_by_id.items()
        if connection_is_dead(state, now=now)
    ]

    if not agent_rows:
        return [], {
            "agent_by_id": agent_by_id,
            "player_by_key": player_by_key,
            "version_by_agent_id": version_by_agent_id,
            "latest_turn_by_match": latest_turn_by_match,
            "dead_ids": dead_ids,
        }

    for agent, player, match, version in agent_rows:
        if version is None:
            logger.warning(
                "next-turn: agent %s (connection %s) has no current version; skipping",
                agent.id,
                connection.id,
            )
            continue
        if agent.provider is None:
            logger.warning("next-turn: AI agent %s has no provider; skipping", agent.id)
            continue
        pin = TurnPin(
            served_by_connection_id=player.served_by_connection_id,
            served_pinned_at=player.served_pinned_at,
        )
        if not can_connection_claim_turn(
            polling_state,
            agent.provider,
            pin,
            now=now,
            connections_by_id=connections_by_id,
        ):
            continue
        player_by_key[(agent.id, match.id)] = player
        agent_by_id[agent.id] = agent
        version_by_agent_id[agent.id] = version
        if match.id not in latest_turn_by_match:
            turn = (
                await db.execute(
                    select(Turn)
                    .where(Turn.match_id == match.id, Turn.resolved_at.is_(None))
                    .order_by(Turn.round.desc(), Turn.turn.desc(), Turn.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if turn is not None:
                latest_turn_by_match[match.id] = turn

    candidates: list[TurnCandidate] = []
    for agent_id, match_id in player_by_key:
        player = player_by_key[(agent_id, match_id)]
        turn = latest_turn_by_match.get(match_id)
        if turn is None:
            continue
        existing = (
            await db.execute(
                select(TurnSubmission.id).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == player.id,
                    TurnSubmission.was_defaulted.is_(False),
                )
            )
        ).first()
        if existing is not None:
            continue
        candidates.append(
            TurnCandidate(
                match_id=match_id,
                round=turn.round,
                turn=turn.turn,
                deadline=_as_aware(turn.deadline_at),
                agent_id=agent_id,
            )
        )
    return candidates, {
        "agent_by_id": agent_by_id,
        "player_by_key": player_by_key,
        "version_by_agent_id": version_by_agent_id,
        "latest_turn_by_match": latest_turn_by_match,
        "dead_ids": dead_ids,
    }


async def _claim_pin(
    db: AsyncSession,
    connection: Connection,
    cand: TurnCandidate,
    ctx: dict[str, object],
    now: datetime,
) -> bool:
    player_by_key = cast(dict[tuple[int, str], Player], ctx["player_by_key"])
    dead_ids = cast(list[int], ctx["dead_ids"])
    player = player_by_key[(cand.agent_id, cand.match_id)]
    claim = cast(
        CursorResult,
        await db.execute(
            update(Player)
            .where(
                Player.id == player.id,
                or_(
                    Player.served_by_connection_id.is_(None),
                    Player.served_by_connection_id == connection.id,
                    Player.served_by_connection_id.in_(dead_ids)
                    if dead_ids
                    else false(),
                ),
            )
            .values(served_by_connection_id=connection.id, served_pinned_at=now)
        ),
    )
    return claim.rowcount == 1


async def _build_turn_payload(
    db: AsyncSession, cand: TurnCandidate, ctx: dict[str, object]
) -> dict[str, object]:
    agent_by_id = cast(dict[int, Agent], ctx["agent_by_id"])
    player_by_key = cast(dict[tuple[int, str], Player], ctx["player_by_key"])
    version_by_agent_id = cast(dict[int, AgentVersion], ctx["version_by_agent_id"])
    latest_turn_by_match = cast(dict[str, Turn], ctx["latest_turn_by_match"])
    agent = agent_by_id[cand.agent_id]
    player = player_by_key[(cand.agent_id, cand.match_id)]
    version = version_by_agent_id[cand.agent_id]
    match = (
        await db.execute(select(Match).where(Match.id == cand.match_id))
    ).scalar_one()
    turn = latest_turn_by_match[cand.match_id]
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == match.id))).scalars().all()
    )
    seat_name_by_agent_id = {player.agent_id: player.seat_name for player in all_players}
    history = _group_into_turns(await _load_public_action_records(db, match.id, all_players))
    scoreboard = [
        {
            "agent_id": seat_name_by_agent_id[p.agent_id],
            "round_score": p.current_round_score,
            "round_wins": p.total_round_wins,
        }
        for p in sorted(all_players, key=lambda p: (-p.current_round_score, p.seat_name))
    ]
    module = get_game_module(match.game)
    your_agent_id = seat_name_by_agent_id[player.agent_id]
    all_agent_ids = sorted(seat_name_by_agent_id.values())
    static = {
        "match_id": match.id,
        "game_id": match.id,
        "game": match.game,
        "rules_version": match.rules_version,
        "rules": module.rules_text(match.total_rounds, match.turns_per_round),
        "base_prompt": module.agent_base_prompt(
            your_agent_id=your_agent_id,
            all_agent_ids=all_agent_ids,
            total_rounds=match.total_rounds,
            turns_per_round=match.turns_per_round,
        ),
        "total_rounds": match.total_rounds,
        "turns_per_round": match.turns_per_round,
        "your_agent_id": your_agent_id,
        "all_agent_ids": all_agent_ids,
        "your_strategy": version.strategy_text,
    }
    if player.coach_note and player.coach_note_round == match.current_round:
        static["coach_note"] = player.coach_note
    current = await _build_current_turn(db, turn)
    payload: dict[str, object] = {
        "status": "your_turn",
        "match_id": match.id,
        "game": match.game,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "provider": agent.provider.value if agent.provider is not None else None,
        "model": version.model,
        "strategy": version.strategy_text,
        "version_no": version.version_no,
        "seat_name": seat_name_by_agent_id[player.agent_id],
        "turn_token": turn.turn_token,
        "agent_turn_token": f"{turn.turn_token}:{agent.id}:{match.id}",
        "static": static,
        "history": history,
        "scoreboard": scoreboard,
        "current": current,
    }
    # Per-game state (omitted for games that supply none, e.g. PD — byte-identical).
    private_state = await module.private_state_for(db, match, player)
    if private_state:
        payload["your_private_state"] = private_state
    public_state = await module.public_state_for(db, match, player)
    if public_state:
        payload["public_state"] = public_state
    return payload


async def _serve_one_turn(
    db: AsyncSession, connection: Connection, now: datetime
) -> dict[str, object] | None:
    candidates, ctx = await _collect_candidates(db, connection, now)
    chosen = select_next_turn(candidates)
    if chosen is None:
        return None
    if not await _claim_pin(db, connection, chosen, ctx, now):
        await db.rollback()
        return None
    await db.commit()
    return await _build_turn_payload(db, chosen, ctx)


async def get_next_turn(
    db: AsyncSession,
    connection: Connection,
    *,
    hold_seconds: float = _LONG_POLL_HOLD_SECONDS,
    interval_seconds: float = _LONG_POLL_INTERVAL_SECONDS,
) -> dict[str, object]:
    held = max(0.0, hold_seconds) > 0.0
    waiting_poll_hint = 2 if held else 30
    deadline = asyncio.get_event_loop().time() + max(0.0, hold_seconds)

    served = await _serve_one_turn(db, connection, datetime.now(timezone.utc))
    if served is not None:
        return served

    connection_id = connection.id
    await db.rollback()

    loop = asyncio.get_event_loop()
    while loop.time() < deadline:
        await asyncio.sleep(max(0.0, min(interval_seconds, deadline - loop.time())))
        async with db_module.SessionLocal() as check_db:
            fresh = (
                await check_db.execute(
                    select(Connection)
                    .options(joinedload(Connection.user).load_only(User.disabled_at))
                    .where(Connection.id == connection_id)
                )
            ).scalar_one_or_none()
            if (
                fresh is None
                or fresh.deleted_at is not None
                or fresh.status == ConnectionStatus.PAUSED
                or fresh.user.disabled_at is not None
            ):
                break
            served = await _serve_one_turn(check_db, fresh, datetime.now(timezone.utc))
        if served is not None:
            return served

    return {"status": "waiting", "next_poll_after_seconds": waiting_poll_hint}


async def get_next_turns(db: AsyncSession, connection: Connection) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    candidates, ctx = await _collect_candidates(db, connection, now)
    ordered = sorted(
        candidates,
        key=lambda cand: (cand.deadline, cand.match_id, cand.round, cand.turn, cand.agent_id),
    )
    claimed = [cand for cand in ordered if await _claim_pin(db, connection, cand, ctx, now)]
    await db.commit()
    if not claimed:
        return {"status": "waiting", "next_poll_after_seconds": 30}
    turns = [await _build_turn_payload(db, cand, ctx) for cand in claimed]
    return {"status": "your_turn", "turns": turns}
