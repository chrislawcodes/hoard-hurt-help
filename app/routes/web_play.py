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

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.templating import templates

from app.agent_prompt import MESSAGE_MAX_LENGTH
from app.api_errors import api_error
from app.aware_datetime import ensure_aware
from app.deps import DbSession, require_user, require_user_with_handle
from app.engine.human_player import get_or_create_human_agent
from app.engine.player_move import record_player_action
from app.games import get as get_game_module
from app.games.base import GameError
from app.identity import word_filter
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.models.user import User
from app.read_models.matches import count_players
from app.routes.web_match_loaders import (
    GameScopedMatchOr404,
    _load_match_or_404,
)
from app.routes.web_support import (
    SEAT_NAME_MAX,
    require_can_view_game,
    unique_seat_name,
)

router = APIRouter(tags=["web"])

Phase = Literal["talk", "act"]


def _unique_human_seat_name(base: str, existing: set[str]) -> str:
    """A unique public seat name from a human's chosen display name.

    Normalizes the human's display name (a missing/blank name becomes "player",
    capped to fit the standings column), then delegates the uniqueness suffixing
    to the shared ``unique_seat_name`` helper.
    """
    normalized = (base or "player").strip()[:SEAT_NAME_MAX] or "player"
    return unique_seat_name(normalized, existing)


def _play_error(code: str, message: str, http: int) -> HTTPException:
    return api_error(status_code=http, code=code, message=message)


async def _active_human_seat(db: DbSession, match_id: str, user_id: int) -> Player | None:
    """The user's active (non-left) human seat in this match, or ``None``.

    The single source of truth for "does this user hold a live human seat here?"
    Shared by the talk/act submit path, the join idempotency check, and leave so
    they can't drift in which seats they count.
    """
    return (
        await db.execute(
            select(Player)
            .join(Agent, Agent.id == Player.agent_id)
            .where(
                Player.match_id == match_id,
                Player.user_id == user_id,
                Agent.kind == AgentKind.HUMAN,
                Player.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _resolve_human_player(db: DbSession, match_id: str, user: User) -> Player:
    """The signed-in user's active human seat in this match, or a friendly error."""
    player = await _active_human_seat(db, match_id, user.id)
    if player is None:
        raise _play_error(
            "NOT_YOUR_SEAT",
            "You're not playing in this match.",
            status.HTTP_403_FORBIDDEN,
        )
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


async def _render_live(request: Request, db: DbSession, match: Match) -> HTMLResponse:
    """Re-render the live region so HTMX swaps the panel into its new state."""
    # Imported lazily to avoid an import cycle (web_viewer doesn't import web_play).
    from app.routes.web_viewer import _game_view_context

    ctx = await _game_view_context(request, db, match)
    return templates.TemplateResponse(request, "fragments/live_region.html", ctx)


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
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    message: Annotated[str, Form()] = "",
) -> HTMLResponse:
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
    return await _render_live(request, db, match)


@router.post("/games/{game}/matches/{match_id}/play/act")
async def play_act(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    action: Annotated[str, Form()],
    target: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
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
    return await _render_live(request, db, match)


# --- join / leave ----------------------------------------------------------


def _viewer_redirect(game: str, match_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/games/{game}/matches/{match_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def seat_human_player(
    db: DbSession, user: User, match: Match, display_name: str | None
) -> bool:
    """Seat *user* as a human in *match* — no agent, no connection, no key.

    Idempotent: a no-op (returns ``False``) if the user already holds an active
    human seat here, otherwise creates the seat and returns ``True``. Only a
    display name (defaulting to the user's handle) is needed; the seat is active
    immediately (never held) and reuses the user's ``kind=human`` agent across
    matches. The caller owns transaction commit and the match-open / access
    checks — this just builds the seat.

    Raises HTTPException(409) if the match is full.
    """
    already = await _active_human_seat(db, match.id, user.id)
    if already is not None:
        return False

    active = await count_players(db, match.id, active_only=True)
    if active >= match.max_players:
        raise HTTPException(status_code=409, detail="This match is full.")

    agent, version = await get_or_create_human_agent(db, user, match.game)
    existing_seats = set(
        (
            await db.execute(
                select(Player.seat_name).where(Player.match_id == match.id)
            )
        )
        .scalars()
        .all()
    )
    seat_name = _unique_human_seat_name(display_name or user.handle or "player", existing_seats)
    db.add(
        Player(
            match_id=match.id,
            user_id=user.id,
            agent_id=agent.id,
            agent_version_id=version.id,
            seat_name=seat_name,
        )
    )
    return True


@router.post("/games/{game}/matches/{match_id}/play/join")
async def play_join(
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    match: GameScopedMatchOr404,
    display_name: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Take a human seat in a scheduled match — no agent, no connection, no key.

    The join screen is the primary entrance (pick "Play manually"); this
    endpoint stays as the direct one-click path and shares ``seat_human_player``
    with it, so the two can't drift. The ``{game}``-slug check is the injected
    ``GameScopedMatchOr404`` dependency (404 "Match not found." on mismatch — same
    body the old inline check returned); ``user`` is listed first so a handle-less
    visitor 303s to /me/handle before that check, as before.
    """
    require_can_view_game(user, match.game)
    if match.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(status_code=409, detail="This match isn't open to join.")

    await seat_human_player(db, user, match, display_name)
    await db.commit()
    return _viewer_redirect(match.game, match.id)


@router.post("/games/{game}/matches/{match_id}/play/leave")
async def play_leave(
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
) -> RedirectResponse:
    """Leave a match. Before start the seat is freed; after start it auto-Hoards.

    In-match leave is one-way for v1: the seat stays in the standings and plays
    Hoard for the rest of the match (set via ``autopilot_at``), so the table is
    never made to wait on a departed human.
    """
    row = await _active_human_seat(db, match_id, user.id)
    if row is None:
        raise _play_error(
            "NOT_YOUR_SEAT", "You're not in this match.", status.HTTP_403_FORBIDDEN
        )
    match = await _load_match_or_404(db, match_id)
    now = datetime.now(timezone.utc)
    if match.state in (GameState.SCHEDULED, GameState.REGISTERING):
        row.left_at = now  # pre-start: free the seat entirely
    elif match.state == GameState.ACTIVE and row.autopilot_at is None:
        row.autopilot_at = now  # in-match: seat auto-Hoards to the end
    await db.commit()
    return _viewer_redirect(game, match_id)
