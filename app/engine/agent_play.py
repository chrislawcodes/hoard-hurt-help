"""Shared agent-play service logic.

This module holds the HTTP agent API's core business logic so the FastAPI
routes can stay thin adapters and the MCP layer can reuse the same turn payload
and submission behavior.

The service is split into focused modules under ``app/engine/``:

* ``agent_play_guards`` — leaf primitives (errors, rate limits, token binding).
* ``agent_play_reads`` — DB-to-payload projection helpers.
* ``agent_play_next_turn`` — connection-level "what do I do next" fan-out.

This module keeps the per-match agent verbs and re-exports the public names so
``from app.engine.agent_play import <name>`` keeps working for both the HTTP
routes and the MCP layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.agent_play_guards import (
    _POLL_WHEN_ACTIVE,
    PollRateState,
    PullRateState,
    _check_poll_rate_limit,
    _check_pull_rate_limit,
    _err,
    _game_for,
    _next_poll_before_start,
    _seat_name_map,
    _validate_agent_match_binding,
    _validate_agent_turn_binding,
)
from app.engine.agent_play_next_turn import (
    agent_identity_for,
    get_next_turn,
    get_next_turns,
)
from app.engine.agent_play_reads import (
    RECENT_HISTORY_TURNS,
    _build_current_turn,
    _existing_message_for_player,
    _existing_submission_for_player,
    _group_into_turns,
    _load_active_phase_turn,
    _load_public_action_records,
    _parse_cursor,
    _public_scoreboard,
    _public_standings,
    sorted_seat_names,
)
from app.engine.connection_activity import increment_turns_played, mark_first_move
from app.games import get as get_game_module
from app.games.base import GameError
from app.identity import word_filter
from app.models.agent_version import AgentVersion
from app.models.connection import Connection
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.ops_events import log_ops_event
from app.schemas.agent import (
    AgentStateResponse,
    ChatLine,
    ChatTranscriptResponse,
    FullStandingsResponse,
    LeaveResponse,
    MessageResponse,
    OpponentHistoryResponse,
    SubmitResponse,
    TalkWindowClosedResponse,
    TurnDetailResponse,
    TurnStatic,
    WaitingResponse,
    YourTurnResponse,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PollRateState",
    "PullRateState",
    "chat_transcript",
    "get_agent_state",
    "get_next_turn",
    "get_next_turns",
    "agent_identity_for",
    "leave_match",
    "opponent_history",
    "poll_turn",
    "standings",
    "submit_action",
    "submit_talk",
    "turn_detail",
    "_pack_move",
]


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
    all_agent_ids = sorted_seat_names(seat_name_by_agent_id)
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
    # Same rolling window as the next-turn fan-out: this per-match poll is served
    # every loop, so it carries only the recent turns and leaves the whole
    # transcript to the on-demand reads (opponent_history / chat / turn_detail).
    history = _group_into_turns(
        await _load_public_action_records(
            db, game.id, all_players, recent_turns=RECENT_HISTORY_TURNS
        )
    )
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
) -> MessageResponse | TalkWindowClosedResponse:
    _validate_agent_turn_binding(
        agent_turn_token,
        turn_token=turn_token,
        match_id=match_id,
        agent_id=player.agent_id,
    )
    game, turn = await _load_active_phase_turn(
        db, match_id, turn_token, "talk", tolerate_phase_advance=True
    )
    if turn.phase != "talk":
        # The talk window already closed and the turn moved on to act. Don't hard-
        # error a late talk — tell the agent calmly to act. The token is unchanged
        # (see `_begin_act_phase`), so it can act with the one it already holds.
        return TalkWindowClosedResponse(
            round=turn.round,
            turn=turn.turn,
            turn_token=turn.turn_token,
        )
    existing = await _existing_message_for_player(db, turn, player)
    if existing is not None and not existing.was_defaulted:
        return MessageResponse(
            received_at=existing.submitted_at or datetime.now(timezone.utc),
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
        )

    module = get_game_module(game.game)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    seat_name_by_agent_id = _seat_name_map(all_players)
    all_agent_ids = sorted_seat_names(seat_name_by_agent_id)
    # Resolve the target to a real seat_name up front, tolerating case and
    # surrounding whitespace, so a move that names a real player the way the AI
    # saw them ("Hannibal", "hannibal", " Hannibal ") lands instead of 400ing on
    # an exact-match miss. Prefer an exact seat_name match, then fall back to a
    # trimmed, case-insensitive one. The canonical seat_name is what validate_move
    # and the resolution below then see. Only PD-style moves (move is None) carry a
    # target_id here; free-form `move` payloads are left untouched.
    target_match: Player | None = None
    canonical_target_id = target_id
    if move is None and target_id is not None:
        needle = target_id.strip().casefold()
        target_match = next(
            (p for p in all_players if p.seat_name == target_id), None
        ) or next(
            (p for p in all_players if p.seat_name.strip().casefold() == needle), None
        )
        if target_match is not None:
            canonical_target_id = target_match.seat_name
    built_move = _pack_move(
        action=action,
        target_id=canonical_target_id,
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
        internal_move["target_id"] = (
            target_match.agent_id if target_match is not None else None
        )
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
