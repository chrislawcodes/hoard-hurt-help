"""Agent API — the HTTP endpoints player AIs call.

Auth: X-Connection-Key header. Errors: spec.md §10 envelope.
"""

import logging
import time
from dataclasses import dataclass
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import (
    DbSession,
    _parse_agent_turn_token,
    require_agent_player,
)
from app.ops_events import log_ops_event
from app.engine.connection_activity import mark_first_move
from app.engine.game_records import Action
from app.games import get as get_game_module
from app.games.base import GameError
from app.identity import word_filter
from app.models.agent_version import AgentVersion
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.schemas.agent import (
    AgentStateResponse,
    ChatLine,
    ChatTranscriptResponse,
    CurrentTurn,
    FullStandingsResponse,
    HistoryAction,
    HistoryTurn,
    LeaveResponse,
    MessageRequest,
    MessageResponse,
    OpponentHistoryResponse,
    ScoreboardRow,
    StandingRow,
    SubmitRequest,
    SubmitResponse,
    TalkMessage,
    TurnDetailResponse,
    TurnStatic,
    WaitingResponse,
    YourTurnResponse,
)

router = APIRouter(tags=["agent"])
logger = logging.getLogger(__name__)

# Per-bot poll throttle (1 Hz). Keyed by Bot.id — a bot owns many players, so
# keying by player would let it dodge the cap by switching games.
_last_poll: dict[int, float] = {}
_MIN_POLL_INTERVAL = 1.0

# Per-(key, pull-kind) throttle for the opt-in detail endpoints. Separate buckets
# so a bot can fetch, say, chat and an opponent's history in the same second, but
# can't spam a single endpoint. Kept apart from the /turn poll bucket.
_last_pull: dict[tuple[int, str], float] = {}
_PULL_MIN_INTERVAL = 1.0

# Recommended client poll cadence (seconds). Each poll is a full LLM inference,
# so we slow agents down when nothing is about to happen and speed them up as a
# turn (or the game start) approaches. Clients honor this via the
# next_poll_after_seconds field on the waiting response.
_POLL_FAR_FROM_START = 30  # game start is further out than the near-start window
_POLL_NEAR_START = 5  # game starts within _NEAR_START_WINDOW_SECONDS
_POLL_WHEN_ACTIVE = 5  # live game: waiting for your turn or for others to submit
_NEAR_START_WINDOW_SECONDS = 180  # "within 3 minutes" → poll more often


def _next_poll_before_start(game: Match) -> int:
    """Seconds a waiting agent should sleep before polling again, pre-start.

    Far from the scheduled start we poll slowly to avoid burning an LLM
    inference every few seconds for nothing. Inside the 3-minute window we
    tighten up so play begins promptly once the game opens.
    """
    seconds_until_start = (
        _as_aware(game.scheduled_start) - datetime.now(timezone.utc)
    ).total_seconds()
    if seconds_until_start > _NEAR_START_WINDOW_SECONDS:
        return _POLL_FAR_FROM_START
    return _POLL_NEAR_START


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


# Joining a game is a web action — the owner picks one of their agents. The
# agent API is play-only; auth resolves the agent's player for a match via
# require_agent_player. No per-game credential is issued.


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


async def _build_current_turn(db, turn: Turn) -> CurrentTurn:
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
    """Load the match and validate that a token can accept this phase's input."""
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


@router.get("/turn")
async def agent_poll(
    match_id: Annotated[str, Path()],
    player: Annotated[Player, Depends(require_agent_player)],
    db: DbSession,
) -> WaitingResponse | YourTurnResponse:
    """Poll for the current turn. Rate-limited to 1 Hz per key."""
    # Rate limit.
    now_t = time.monotonic()
    last = _last_poll.get(player.agent_id, 0.0)
    if now_t - last < _MIN_POLL_INTERVAL:
        raise _err("RATE_LIMITED", "Polling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
    _last_poll[player.agent_id] = now_t

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

    # ACTIVE — find the latest open (unresolved) turn for this game.
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

    # Has this player already submitted for this turn?
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

    # Build the full your_turn payload.
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
    # Raw, cache-friendly payload: the stable `static` + append-only `history`
    # form a prefix a client can prompt-cache; the volatile `scoreboard` and
    # `current` come last. Nothing is pre-digested — the agent reads the moves
    # and messages and does its own analysis.
    history = _group_into_turns(await _load_public_action_records(db, game.id, all_players))
    return YourTurnResponse(
        static=static,
        history=history,
        scoreboard=_public_scoreboard(all_players),
        current=await _build_current_turn(db, turn),
    )


@router.post("/message", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
async def agent_message(
    match_id: Annotated[str, Path()],
    body: MessageRequest,
    agent_turn_token: Annotated[str, Query()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> MessageResponse:
    """Submit this turn's talk-phase message. Idempotent on (turn_token, player_id)."""
    _validate_agent_turn_binding(
        agent_turn_token,
        turn_token=body.turn_token,
        match_id=match_id,
        agent_id=player.agent_id,
    )
    game, turn = await _load_active_phase_turn(db, match_id, body.turn_token, "talk")
    existing = await _existing_message_for_player(db, turn, player)
    if existing is not None and not existing.was_defaulted:
        return MessageResponse(
            received_at=existing.submitted_at or datetime.now(timezone.utc),
            phase_resolves_at=turn.deadline_at,
        )

    module = get_game_module(game.game)
    # Public text is censored, not blocked: the message still posts with any
    # bad word masked to **** (handles/agent names are rejected instead).
    await module.record_message(
        db,
        turn,
        player,
        word_filter.mask(body.message),
        word_filter.mask(body.thinking),
        existing=existing,
        is_connector_fallback=body.is_connector_fallback,
    )
    if body.is_connector_fallback:
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


@router.post("/submit", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def agent_submit(
    match_id: Annotated[str, Path()],
    body: SubmitRequest,
    agent_turn_token: Annotated[str, Query()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> SubmitResponse:
    """Submit this turn's action. Idempotent on (turn_token, player_id)."""
    _validate_agent_turn_binding(
        agent_turn_token,
        turn_token=body.turn_token,
        match_id=match_id,
        agent_id=player.agent_id,
    )
    game, turn = await _load_active_phase_turn(db, match_id, body.turn_token, "act")
    existing = await _existing_submission_for_player(db, turn, player)
    if existing is not None and not existing.was_defaulted:
        return SubmitResponse(
            received_at=existing.submitted_at or datetime.now(timezone.utc),
            turn_will_resolve_at=turn.deadline_at,
        )

    # Validate + record through the game module — the platform never hard-codes
    # a game's move vocabulary. The module raises GameError with its own
    # code/message/details, which map straight onto the standard error envelope.
    module = get_game_module(game.game)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    seat_name_by_agent_id = _seat_name_map(all_players)
    all_agent_ids = sorted(seat_name_by_agent_id.values())
    move = {
        "action": body.action,
        "target_id": body.target_id,
        # Censor bad words in the public message/reasoning (mask, don't block).
        "message": word_filter.mask(body.message),
        "thinking": word_filter.mask(body.thinking),
    }
    try:
        module.validate_move(
            move, your_agent_id=player.seat_name, all_agent_ids=all_agent_ids
        )
    except GameError as exc:
        raise _err(
            exc.code, exc.message, status.HTTP_400_BAD_REQUEST, exc.details
        ) from exc
    internal_move: dict[str, object] = {**move}
    if body.target_id is not None:
        target_player = next(
            (candidate for candidate in all_players if candidate.seat_name == body.target_id),
            None,
        )
        internal_move["target_id"] = target_player.agent_id if target_player else None
    await module.record_submission(
        db, turn, player, internal_move,
        existing=existing,
        is_connector_fallback=body.is_connector_fallback,
    )
    if body.is_connector_fallback:
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

    # Announce the bot's first real move so an open bot-detail page lights up.
    # No-op after the first move. (MCP submit_action proxies here, so this one
    # hook covers that path too.)
    await mark_first_move(db, player.agent_id)

    return SubmitResponse(
        received_at=datetime.now(timezone.utc),
        turn_will_resolve_at=turn.deadline_at,
    )




@router.get("/state", response_model=AgentStateResponse)
async def agent_state(
    match_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
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
        s = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == open_turn.id,
                    TurnSubmission.player_id == player.id,
                )
            )
        ).scalar_one_or_none()
        you_submitted = s is not None and not s.was_defaulted

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




@router.post("/leave", response_model=LeaveResponse)
async def agent_leave(
    match_id: Annotated[str, Path()],
    agent_turn_token: Annotated[str, Query()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
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


# --- Pull-on-demand detail endpoints (opt-in; rate-limited per (key, kind)) ---


def _pull_rate_limiter(bucket: str) -> Callable[[Player], Awaitable[Player]]:
    """Build a dependency that throttles one pull kind to 1 Hz per key."""

    async def dep(player: Annotated[Player, Depends(require_agent_player)]) -> Player:
        now_t = time.monotonic()
        last = _last_pull.get((player.agent_id, bucket), 0.0)
        if now_t - last < _PULL_MIN_INTERVAL:
            raise _err("RATE_LIMITED", "Pulling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
        _last_pull[(player.agent_id, bucket)] = now_t
        return player

    return dep


async def _game_for(player: Player, match_id: str, db: AsyncSession) -> Match:
    return (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()


def _group_into_turns(actions: Sequence[_PublicActionRecord]) -> list[HistoryTurn]:
    by_rt: dict[tuple[int, int], list[HistoryAction]] = {}
    for a in sorted(actions, key=lambda x: (x.round, x.turn)):
        by_rt.setdefault((a.round, a.turn), []).append(
            HistoryAction(
                agent_id=a.actor_id,
                action=a.action,
                target_id=a.target_id,
                message=a.message,
                points_delta=a.points_delta,
            )
        )
    return [HistoryTurn(round=r, turn=t, actions=acts) for (r, t), acts in sorted(by_rt.items())]


def _parse_cursor(since: str | None) -> tuple[int, int] | None:
    if not since:
        return None
    parts = since.split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise _err("INVALID_CURSOR", "since must be 'round.turn'.", status.HTTP_400_BAD_REQUEST)
    return int(parts[0]), int(parts[1])


@router.get("/history/opponents/{opponent_id}", response_model=OpponentHistoryResponse)
async def agent_opponent_history(
    match_id: Annotated[str, Path()],
    opponent_id: Annotated[str, Path()],  # public seat_name, never the internal agent id
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("opponent_history"))],
) -> OpponentHistoryResponse:
    """PULL: every action between you and one opponent, grouped by turn."""
    game = await _game_for(player, match_id, db)
    opp = (
        await db.execute(
            select(Player).where(Player.match_id == game.id, Player.seat_name == opponent_id)
        )
    ).scalar_one_or_none()
    if opp is None:
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
        a
        for a in public_actions
        if a.actor_id == opp.seat_name or (a.actor_id == you and a.target_id == opp.seat_name)
    ]
    return OpponentHistoryResponse(
        opponent_id=opp.seat_name,
        turns=_group_into_turns(actions),
    )


@router.get("/chat", response_model=ChatTranscriptResponse)
async def agent_chat(
    match_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("chat"))],
    since: Annotated[str | None, Query()] = None,
) -> ChatTranscriptResponse:
    """PULL: the public chat transcript, optionally only after a 'round.turn' cursor."""
    game = await _game_for(player, match_id, db)
    cursor = _parse_cursor(since)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    lines: list[ChatLine] = []
    for a in sorted(
        await _load_public_action_records(db, game.id, all_players),
        key=lambda x: (x.round, x.turn),
    ):
        if a.was_defaulted or not a.message:
            continue
        if cursor is not None and (a.round, a.turn) <= cursor:
            continue
        lines.append(
            ChatLine(
                round=a.round,
                turn=a.turn,
                from_agent_id=a.actor_id,
                target_id=a.target_id,
                message=a.message,
            )
        )
    next_cursor = f"{lines[-1].round}.{lines[-1].turn}" if lines else since
    return ChatTranscriptResponse(since=since, messages=lines, next_cursor=next_cursor)


@router.get("/turns/{round}/{turn}", response_model=TurnDetailResponse)
async def agent_turn_detail(
    match_id: Annotated[str, Path()],
    round: Annotated[int, Path()],
    turn: Annotated[int, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("turn_detail"))],
) -> TurnDetailResponse:
    """PULL: every player's action+message+points for one resolved turn."""
    game = await _game_for(player, match_id, db)
    t = (
        await db.execute(
            select(Turn).where(
                Turn.match_id == game.id,
                Turn.round == round,
                Turn.turn == turn,
                Turn.resolved_at.is_not(None),
            )
    )
    ).scalar_one_or_none()
    if t is None:
        raise _err("NOT_FOUND", "No such resolved turn.", status.HTTP_404_NOT_FOUND)
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == game.id))).scalars().all()
    )
    these = [
        a
        for a in await _load_public_action_records(db, game.id, all_players)
        if a.round == round and a.turn == turn
    ]
    grouped = _group_into_turns(these)
    actions = grouped[0].actions if grouped else []
    return TurnDetailResponse(round=round, turn=turn, actions=actions)


@router.get("/standings", response_model=FullStandingsResponse)
async def agent_standings(
    match_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("standings"))],
) -> FullStandingsResponse:
    """PULL: the full standings, every active player ranked."""
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
