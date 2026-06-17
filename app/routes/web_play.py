"""Human play routes — a seated human submits a turn from the game viewer.

These are the human counterpart to the agent submit path (`agent_api` /
`agent_play`). The difference is purely auth and how the move arrives: a human is
a signed-in user (no connection, no turn token), and the move comes from the play
panel as a form POST. The actual recording goes through the same `GameModule`
verbs every player uses, via the shared `record_player_action` helper, so the
human path can't drift from the bot/agent paths.

Re-submitting within an open phase **replaces** the pending choice (a human can
change their mind until the clock ends), which is why these routes pass the
existing row to the module rather than returning early like the idempotent agent
path does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Path, status
from sqlalchemy import select

from app.agent_prompt import MESSAGE_MAX_LENGTH
from app.aware_datetime import ensure_aware
from app.deps import DbSession, require_user
from app.engine.player_move import record_player_action
from app.games import get as get_game_module
from app.games.base import GameError
from app.identity import word_filter
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.models.user import User

router = APIRouter(tags=["web"])

Phase = Literal["talk", "act"]


def _play_error(code: str, message: str, http: int) -> HTTPException:
    return HTTPException(
        status_code=http,
        detail={"error": {"code": code, "message": message, "details": {}}},
    )


async def _resolve_human_player(db: DbSession, match_id: str, user: User) -> Player:
    """The signed-in user's active human seat in this match, or a friendly error."""
    row = (
        await db.execute(
            select(Player, Agent)
            .join(Agent, Agent.id == Player.agent_id)
            .where(
                Player.match_id == match_id,
                Player.user_id == user.id,
                Agent.kind == AgentKind.HUMAN,
                Player.left_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise _play_error(
            "NOT_YOUR_SEAT",
            "You're not playing in this match.",
            status.HTTP_403_FORBIDDEN,
        )
    player, _agent = row
    if player.autopilot_at is not None:
        raise _play_error(
            "LEFT_MATCH",
            "You left this match — your seat is playing Hoard on autopilot.",
            status.HTTP_409_CONFLICT,
        )
    return player


async def _load_open_turn(
    db: DbSession, match_id: str, phase: Phase
) -> tuple[Match, Turn]:
    """The match's current open turn in ``phase``, or a friendly error."""
    game = (
        await db.execute(select(Match).where(Match.id == match_id))
    ).scalar_one_or_none()
    if game is None:
        raise _play_error("NO_MATCH", "Match not found.", status.HTTP_404_NOT_FOUND)
    if game.state != GameState.ACTIVE:
        raise _play_error(
            "GAME_NOT_ACTIVE", "This match isn't active.", status.HTTP_409_CONFLICT
        )
    turn = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.match_id == match_id, Turn.resolved_at.is_(None))
                .order_by(Turn.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if turn is None or turn.phase != phase:
        raise _play_error(
            "TURN_RESOLVED",
            "That turn already resolved — hang tight for the next one.",
            status.HTTP_409_CONFLICT,
        )
    if datetime.now(timezone.utc) >= ensure_aware(turn.deadline_at):
        raise _play_error(
            "TURN_RESOLVED",
            "That turn already resolved — hang tight for the next one.",
            status.HTTP_410_GONE,
        )
    return game, turn


async def _all_players(db: DbSession, match_id: str) -> list[Player]:
    return list(
        (
            await db.execute(select(Player).where(Player.match_id == match_id))
        )
        .scalars()
        .all()
    )


@router.post("/games/{game}/matches/{match_id}/play/talk")
async def play_talk(
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    message: Annotated[str, Form()] = "",
) -> dict[str, object]:
    """Record (or replace) the human's talk message for the open turn.

    An empty message is a valid Pass. Re-posting before the phase resolves
    replaces the pending message.
    """
    if len(message) > MESSAGE_MAX_LENGTH:
        raise _play_error(
            "MESSAGE_TOO_LONG",
            f"Keep it under {MESSAGE_MAX_LENGTH} characters.",
            status.HTTP_400_BAD_REQUEST,
        )
    player = await _resolve_human_player(db, match_id, user)
    match, turn = await _load_open_turn(db, match_id, "talk")
    module = get_game_module(match.game)

    existing = (
        await db.execute(
            select(TurnMessage).where(
                TurnMessage.turn_id == turn.id, TurnMessage.player_id == player.id
            )
        )
    ).scalar_one_or_none()
    await module.record_message(
        db,
        turn,
        player,
        word_filter.mask(message),
        "",
        existing=existing,
    )
    await db.commit()
    return {"ok": True, "phase": "talk", "resolves_at": turn.deadline_at.isoformat()}


@router.post("/games/{game}/matches/{match_id}/play/act")
async def play_act(
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    action: Annotated[str, Form()],
    target: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    """Record (or replace) the human's action for the open turn.

    ``action`` is HOARD/HELP/HURT; ``target`` is the chosen opponent's public
    seat name (required for HELP/HURT, omitted for HOARD). Re-posting before the
    deadline replaces the pending action — this is also the endpoint the panel's
    near-deadline auto-submit of the current selection posts to.
    """
    player = await _resolve_human_player(db, match_id, user)
    match, turn = await _load_open_turn(db, match_id, "act")
    module = get_game_module(match.game)

    players = await _all_players(db, match_id)
    all_seat_names = sorted(p.seat_name for p in players)
    agent_id_by_seat_name = {p.seat_name: p.agent_id for p in players}

    normalized_target = target or None  # treat empty string as no target
    move: dict[str, object] = {
        "action": action.upper(),
        "target_id": normalized_target,
        "message": "",
        "thinking": "",
    }
    existing = (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id,
                TurnSubmission.player_id == player.id,
            )
        )
    ).scalar_one_or_none()
    try:
        await record_player_action(
            db,
            module,
            turn,
            player,
            move=move,
            all_seat_names=all_seat_names,
            agent_id_by_seat_name=agent_id_by_seat_name,
            existing=existing,
        )
    except GameError as exc:
        raise _play_error(exc.code, exc.message, status.HTTP_400_BAD_REQUEST) from exc
    await db.commit()
    return {"ok": True, "phase": "act", "resolves_at": turn.deadline_at.isoformat()}
