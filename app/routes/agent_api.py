"""Agent API — the HTTP endpoints player AIs call.

Auth: X-Agent-Key header. Errors: spec.md §10 envelope.
"""

import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select

from app.deps import DbSession, require_bot_player
from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.engine.opponent_stats import rank_players
from app.engine.rules import RULES_TEXT_V1, RULES_VERSION
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.turn import Turn, TurnSubmission
from app.schemas.agent import (
    AgentStateResponse,
    ChatLine,
    ChatTranscriptResponse,
    CurrentTurn,
    FullStandingsResponse,
    HistoryAction,
    HistoryTurn,
    LeaveResponse,
    OpponentHistoryResponse,
    ScoreboardRow,
    StandingRow,
    SubmitRequest,
    SubmitResponse,
    TurnDetailResponse,
    TurnStatic,
    WaitingResponse,
    YourTurnResponse,
)

router = APIRouter(prefix="/api/games/{game_id}", tags=["agent"])

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


def _next_poll_before_start(game: Game) -> int:
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


# Joining a game is a web action — the owner picks one of their bots (see
# app/routes/web.py). The agent API is play-only; auth resolves the bot's
# player for a game via require_bot_player. No per-game credential is issued.


async def _build_scoreboard(db, game: Game) -> list[ScoreboardRow]:
    players = (
        (await db.execute(select(Player).where(Player.game_id == game.id))).scalars().all()
    )
    return [
        ScoreboardRow(
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            round_wins=p.total_round_wins,
        )
        for p in players
    ]


async def _load_players(db, game: Game) -> list[PlayerRecord]:
    """Active (non-left) players as DB-free records for the summary engine."""
    rows = (
        (
            await db.execute(
                select(Player).where(Player.game_id == game.id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return [
        PlayerRecord(
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            total_score=p.total_round_score,
            round_wins=p.total_round_wins,
        )
        for p in rows
    ]


async def _load_action_records(db, game: Game) -> list[ActionRecord]:
    """Every resolved submission as a DB-free ActionRecord, ids → agent names.

    All players (including any who left) are mapped so historical actors/targets
    still resolve to a name.
    """
    turns = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.game_id == game.id, Turn.resolved_at.is_not(None))
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
        for p in (await db.execute(select(Player).where(Player.game_id == game.id)))
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
                message=s.message,
                points_delta=s.points_delta,
                round_score_after=s.round_score_after,
                was_defaulted=s.was_defaulted,
            )
        )
    return records


@router.get("/turn")
async def agent_poll(
    game_id: Annotated[str, Path()],
    player: Annotated[Player, Depends(require_bot_player)],
    db: DbSession,
) -> WaitingResponse | YourTurnResponse:
    """Poll for the current turn. Rate-limited to 1 Hz per key."""
    # Rate limit.
    now_t = time.monotonic()
    last = _last_poll.get(player.bot_id, 0.0)
    if now_t - last < _MIN_POLL_INTERVAL:
        raise _err("RATE_LIMITED", "Polling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
    _last_poll[player.bot_id] = now_t

    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()

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
            .where(Turn.game_id == game.id, Turn.resolved_at.is_(None))
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
    static = TurnStatic(
        game_id=game.id,
        rules_version=RULES_VERSION,
        rules=RULES_TEXT_V1,
        total_rounds=game.total_rounds,
        turns_per_round=game.turns_per_round,
        your_agent_id=player.agent_id,
        all_agent_ids=sorted(p.agent_id for p in all_players),
        your_strategy=latest_strategy.prompt_text if latest_strategy else None,
    )
    # Raw, cache-friendly payload: the stable `static` + append-only `history`
    # form a prefix a client can prompt-cache; the volatile `scoreboard` and
    # `current` come last. Nothing is pre-digested — the agent reads the moves
    # and messages and does its own analysis.
    history = _group_into_turns(await _load_action_records(db, game))
    return YourTurnResponse(
        static=static,
        history=history,
        scoreboard=await _build_scoreboard(db, game),
        current=CurrentTurn(
            round=turn.round,
            turn=turn.turn,
            deadline=turn.deadline_at,
            turn_token=turn.turn_token,
        ),
    )


# require_bot_player resolves the bot key + game_id to the Player; FastAPI dep injection handles it.


@router.post("/submit", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def agent_submit(
    game_id: Annotated[str, Path()],
    body: SubmitRequest,
    db: DbSession,
    player: Annotated[Player, Depends(require_bot_player)],
) -> SubmitResponse:
    """Submit this turn's action. Idempotent on (turn_token, player_id)."""
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()
    if game.state != GameState.ACTIVE:
        raise _err(
            "GAME_NOT_ACTIVE",
            "Game is not active.",
            status.HTTP_409_CONFLICT,
        )

    turn = (
        await db.execute(
            select(Turn).where(Turn.game_id == game.id, Turn.turn_token == body.turn_token)
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
    if datetime.now(timezone.utc) >= _as_aware(turn.deadline_at):
        raise _err("DEADLINE_PASSED", "Submission past deadline.", status.HTTP_410_GONE)

    # Idempotency: a prior submission with the same token returns same shape.
    existing = (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id,
                TurnSubmission.player_id == player.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and not existing.was_defaulted:
        return SubmitResponse(
            received_at=existing.submitted_at or datetime.now(timezone.utc),
            turn_will_resolve_at=turn.deadline_at,
        )

    # Validate action + target.
    target_player_id: int | None = None
    if body.action == "HOARD":
        if body.target_id is not None:
            raise _err(
                "TARGET_NOT_ALLOWED_FOR_HOARD",
                "HOARD must not have a target.",
                status.HTTP_400_BAD_REQUEST,
            )
    else:  # HELP or HURT
        if body.target_id is None:
            raise _err(
                "MISSING_TARGET",
                "HELP/HURT requires target_id.",
                status.HTTP_400_BAD_REQUEST,
            )
        if body.target_id == player.agent_id:
            raise _err(
                "INVALID_TARGET",
                "Cannot target self.",
                status.HTTP_400_BAD_REQUEST,
                details={"reason": "self_target"},
            )
        target = (
            await db.execute(
                select(Player).where(
                    Player.game_id == game.id, Player.agent_id == body.target_id
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise _err(
                "INVALID_TARGET",
                "Target not in this game.",
                status.HTTP_400_BAD_REQUEST,
                details={"reason": "unknown_agent"},
            )
        target_player_id = target.id

    if existing is not None:
        # Replace the defaulted row.
        existing.action = body.action
        existing.target_player_id = target_player_id
        existing.message = body.message
        existing.was_defaulted = False
        existing.submitted_at = datetime.now(timezone.utc)
    else:
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=player.id,
                action=body.action,
                target_player_id=target_player_id,
                message=body.message,
                submitted_at=datetime.now(timezone.utc),
            )
        )
    await db.commit()

    return SubmitResponse(
        received_at=datetime.now(timezone.utc),
        turn_will_resolve_at=turn.deadline_at,
    )




@router.get("/state", response_model=AgentStateResponse)
async def agent_state(
    game_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_bot_player)],
) -> AgentStateResponse:
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()
    open_turn = (
        await db.execute(
            select(Turn)
            .where(Turn.game_id == game.id, Turn.resolved_at.is_(None))
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
        (await db.execute(select(Player).where(Player.game_id == game.id))).scalars().all()
    )
    return AgentStateResponse(
        game_id=game.id,
        game_state=game.state.value,
        current_round=game.current_round,
        current_turn=game.current_turn,
        deadline=open_turn.deadline_at if open_turn else None,
        you_have_submitted_current_turn=you_submitted,
        scoreboard=await _build_scoreboard(db, game),
        all_agent_ids=sorted(p.agent_id for p in all_players),
    )




@router.post("/leave", response_model=LeaveResponse)
async def agent_leave(
    game_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_bot_player)],
) -> LeaveResponse:
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()
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

    async def dep(player: Annotated[Player, Depends(require_bot_player)]) -> Player:
        now_t = time.monotonic()
        last = _last_pull.get((player.bot_id, bucket), 0.0)
        if now_t - last < _PULL_MIN_INTERVAL:
            raise _err("RATE_LIMITED", "Pulling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
        _last_pull[(player.bot_id, bucket)] = now_t
        return player

    return dep


async def _game_for(player: Player, game_id: str, db) -> Game:
    return (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()


def _group_into_turns(actions: Sequence[ActionRecord]) -> list[HistoryTurn]:
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
    game_id: Annotated[str, Path()],
    opponent_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("opponent_history"))],
) -> OpponentHistoryResponse:
    """PULL: every action between you and one opponent, grouped by turn."""
    game = await _game_for(player, game_id, db)
    opp = (
        await db.execute(
            select(Player).where(Player.game_id == game.id, Player.agent_id == opponent_id)
        )
    ).scalar_one_or_none()
    if opp is None:
        raise _err(
            "INVALID_TARGET",
            "Opponent not in this game.",
            status.HTTP_400_BAD_REQUEST,
            details={"reason": "unknown_agent"},
        )
    you = player.agent_id
    actions = [
        a
        for a in await _load_action_records(db, game)
        if a.actor_id == opponent_id or (a.actor_id == you and a.target_id == opponent_id)
    ]
    return OpponentHistoryResponse(opponent_id=opponent_id, turns=_group_into_turns(actions))


@router.get("/chat", response_model=ChatTranscriptResponse)
async def agent_chat(
    game_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("chat"))],
    since: Annotated[str | None, Query()] = None,
) -> ChatTranscriptResponse:
    """PULL: the public chat transcript, optionally only after a 'round.turn' cursor."""
    game = await _game_for(player, game_id, db)
    cursor = _parse_cursor(since)
    lines: list[ChatLine] = []
    for a in sorted(await _load_action_records(db, game), key=lambda x: (x.round, x.turn)):
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
    game_id: Annotated[str, Path()],
    round: Annotated[int, Path()],
    turn: Annotated[int, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("turn_detail"))],
) -> TurnDetailResponse:
    """PULL: every player's action+message+points for one resolved turn."""
    game = await _game_for(player, game_id, db)
    t = (
        await db.execute(
            select(Turn).where(
                Turn.game_id == game.id,
                Turn.round == round,
                Turn.turn == turn,
                Turn.resolved_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if t is None:
        raise _err("NOT_FOUND", "No such resolved turn.", status.HTTP_404_NOT_FOUND)
    these = [a for a in await _load_action_records(db, game) if a.round == round and a.turn == turn]
    grouped = _group_into_turns(these)
    actions = grouped[0].actions if grouped else []
    return TurnDetailResponse(round=round, turn=turn, actions=actions)


@router.get("/standings", response_model=FullStandingsResponse)
async def agent_standings(
    game_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(_pull_rate_limiter("standings"))],
) -> FullStandingsResponse:
    """PULL: the full standings, every active player ranked."""
    game = await _game_for(player, game_id, db)
    ranked = rank_players(await _load_players(db, game))
    rows = [
        StandingRow(agent_id=p.agent_id, round_score=p.round_score, rank=i + 1)
        for i, p in enumerate(ranked)
    ]
    return FullStandingsResponse(rows=rows, total_players=len(rows))


