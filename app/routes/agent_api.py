"""Agent API — the HTTP endpoints player AIs call.

Auth: X-Agent-Key header. Errors: spec.md §10 envelope.
"""

import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select

from app.deps import DbSession, require_agent_key
from app.engine.rules import RULES_TEXT_V1, RULES_VERSION
from app.engine.tokens import generate_agent_key, hash_agent_key
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.schemas.agent import (
    AgentStateResponse,
    HistoryAction,
    HistoryTurn,
    JoinRequest,
    JoinResponse,
    LeaveResponse,
    ScoreboardRow,
    SubmitRequest,
    SubmitResponse,
    TurnDynamic,
    TurnStatic,
    WaitingResponse,
    YourTurnResponse,
)

router = APIRouter(prefix="/api/games/{game_id}", tags=["agent"])

# Per-key poll throttle (1 Hz). Keyed by Player.id.
_last_poll: dict[int, float] = {}
_MIN_POLL_INTERVAL = 1.0


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


@router.post("/join", response_model=JoinResponse, status_code=status.HTTP_201_CREATED)
async def agent_join(
    game_id: Annotated[str, Path()],
    body: JoinRequest,
    db: DbSession,
) -> JoinResponse:
    """Register a new agent for a game. Returns the per-game key once."""
    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if game is None:
        raise _err("GAME_NOT_FOUND", "Game not found.", status.HTTP_404_NOT_FOUND)
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise _err(
            "GAME_NOT_OPEN_FOR_REGISTRATION",
            "Game is not accepting registrations.",
            status.HTTP_409_CONFLICT,
        )

    # Name uniqueness within game.
    existing = (
        await db.execute(
            select(Player).where(Player.game_id == game.id, Player.agent_id == body.display_name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise _err(
            "INVALID_DISPLAY_NAME",
            "Display name already taken in this game.",
            status.HTTP_400_BAD_REQUEST,
        )

    # Player count cap.
    count = len(
        (await db.execute(select(Player).where(Player.game_id == game.id))).scalars().all()
    )
    if count >= game.max_players:
        raise _err("GAME_FULL", "Game has reached max players.", status.HTTP_409_CONFLICT)

    # The Agent API skips Google auth — we create a synthetic User for non-browser joiners.
    # For real flow, the web join route uses the signed-in User.
    user = User(
        google_sub=f"agent-direct-{body.display_name}-{game.id}",
        email=f"{body.display_name.lower()}@agent-direct.{game.id}.local",
        name=body.display_name,
    )
    db.add(user)
    await db.flush()

    key = generate_agent_key()
    player = Player(
        game_id=game.id,
        user_id=user.id,
        agent_id=body.display_name,
        agent_key_hash=hash_agent_key(key),
        model_self_report=body.model_self_report,
    )
    db.add(player)
    await db.flush()
    db.add(
        StrategyPrompt(
            player_id=player.id,
            prompt_text=body.strategy_prompt,
            is_default=False,
        )
    )
    await db.commit()

    return JoinResponse(
        game_id=game.id,
        agent_id=player.agent_id,
        agent_key=key,
        poll_url=f"/api/games/{game.id}/turn",
        submit_url=f"/api/games/{game.id}/submit",
        scheduled_start=game.scheduled_start,
        per_turn_deadline_seconds=game.per_turn_deadline_seconds,
    )


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


async def _build_history(db, game: Game) -> list[HistoryTurn]:
    """Every resolved turn in order with each player's action."""
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
    players_by_id = {
        p.id: p
        for p in (await db.execute(select(Player).where(Player.game_id == game.id)))
        .scalars()
        .all()
    }
    out: list[HistoryTurn] = []
    for t in turns:
        subs = (
            (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == t.id)))
            .scalars()
            .all()
        )
        actions: list[HistoryAction] = []
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if actor is None:
                continue
            actions.append(
                HistoryAction(
                    agent_id=actor.agent_id,
                    action=s.action,  # type: ignore[arg-type]
                    target_id=target.agent_id if target else None,
                    message=s.message,
                    points_delta=s.points_delta,
                )
            )
        out.append(HistoryTurn(round=t.round, turn=t.turn, actions=actions))
    return out


@router.get("/turn")
async def agent_poll(
    game_id: Annotated[str, Path()],
    player: Annotated[Player, Depends(require_agent_key)],
    db: DbSession,
) -> WaitingResponse | YourTurnResponse:
    """Poll for the current turn. Rate-limited to 1 Hz per key."""
    # Rate limit.
    now_t = time.monotonic()
    last = _last_poll.get(player.id, 0.0)
    if now_t - last < _MIN_POLL_INTERVAL:
        raise _err("RATE_LIMITED", "Polling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
    _last_poll[player.id] = now_t

    if player.game_id != game_id:
        raise _err("INVALID_KEY", "Key not for this game.", status.HTTP_401_UNAUTHORIZED)

    game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()

    if game.state in (GameState.SCHEDULED, GameState.REGISTERING):
        return WaitingResponse(
            reason="game_not_started",
            game_state=game.state.value,
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
    dynamic = TurnDynamic(
        current_round=turn.round,
        current_turn=turn.turn,
        deadline=turn.deadline_at,
        turn_token=turn.turn_token,
        scoreboard=await _build_scoreboard(db, game),
        history=await _build_history(db, game),
    )
    return YourTurnResponse(static=static, dynamic=dynamic)


# require_agent_key produces the Player; FastAPI dep injection handles it.


@router.post("/submit", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def agent_submit(
    game_id: Annotated[str, Path()],
    body: SubmitRequest,
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_key)],
) -> SubmitResponse:
    """Submit this turn's action. Idempotent on (turn_token, player_id)."""
    if player.game_id != game_id:
        raise _err("INVALID_KEY", "Key not for this game.", status.HTTP_401_UNAUTHORIZED)

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
    player: Annotated[Player, Depends(require_agent_key)],
) -> AgentStateResponse:
    if player.game_id != game_id:
        raise _err("INVALID_KEY", "Key not for this game.", status.HTTP_401_UNAUTHORIZED)
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
    player: Annotated[Player, Depends(require_agent_key)],
) -> LeaveResponse:
    if player.game_id != game_id:
        raise _err("INVALID_KEY", "Key not for this game.", status.HTTP_401_UNAUTHORIZED)
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


