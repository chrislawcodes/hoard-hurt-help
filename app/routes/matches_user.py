"""Match creation and ownership routes.

This is the one create path. Every signed-in player picks their own player
counts, deadline, rounds and turns here; a platform admin gets the same form
plus the per-match rule switch and the under-construction games. There is no
separate admin create route.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.api_errors import api_error
from app.aware_datetime import ensure_aware
from app.config import settings
from app.deps import DbSession, require_platform_admin, require_user
from app.engine.match_creation import (
    create_match_with_state,
    game_owns_match_config,
    player_count_error,
    state_config_for,
)
from app.engine.match_deletion import cancel_match, delete_match
from app.engine.user_match_start import start_match_for_user, viewer_start_eligibility
from app.games import GameError, get as get_game_module
from app.games.base import GameModule
from app.games.hoard_hurt_help.rules import MutualHelpMode
from app.models.match import GameState, Match
from app.models.user import User, UserRole
from app.routes.web_match_loaders import (
    GameScopedMatchOr404,
    _load_match_or_404,
)
from app.routes.web_support import (
    _is_any_admin,
    require_can_view_game,
)
from app.templating import templates

router = APIRouter(tags=["web"])

# The band this route accepts for rounds and turns, independent of what a game
# module suggests. Liar's Dice defaults to 64 rounds of 256 turns, which is a
# tournament shape no one creates by hand.
_MIN_ROUNDS, _MAX_ROUNDS = 3, 20
_MIN_TURNS, _MAX_TURNS = 3, 20
# Matches the bound CreateGameRequest already enforces on the JSON API.
_MIN_DEADLINE, _MAX_DEADLINE = 5, 600
_MIN_DICE, _MAX_DICE = 1, 20

# Fallback prefill for a game whose own defaults sit outside this route's bands.
_FALLBACK_DEADLINE = 75
_FALLBACK_ROUNDS = 5
_FALLBACK_TURNS = 7


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _form_defaults(module: GameModule) -> dict[str, int]:
    """Prefill values for the create form.

    Player counts come straight from the game module. Rounds and turns are
    clamped into the band this route accepts, so the form a player opens can
    always be submitted as-is — Liar's Dice's own 64/256 would be rejected by
    the very route that rendered them.
    """
    cfg = module.config_defaults()
    return {
        "min_players": cfg.min_players,
        "max_players": cfg.max_players,
        "per_turn_deadline_seconds": _clamp(
            cfg.per_turn_deadline_seconds or _FALLBACK_DEADLINE,
            _MIN_DEADLINE,
            _MAX_DEADLINE,
        ),
        "total_rounds": _clamp(
            cfg.total_rounds or _FALLBACK_ROUNDS, _MIN_ROUNDS, _MAX_ROUNDS
        ),
        "turns_per_round": _clamp(
            cfg.turns_per_round or _FALLBACK_TURNS, _MIN_TURNS, _MAX_TURNS
        ),
    }


def _load_game_module_or_404(game: str) -> GameModule:
    try:
        return get_game_module(game)
    except GameError as exc:
        raise HTTPException(status_code=404, detail="Game not found.") from exc


def _load_visible_game_module_or_404(game: str, user: User | None) -> GameModule:
    """Like `_load_game_module_or_404`, but an admin-only (under-construction)
    game is invisible (404) to non-admins so they can't create matches for it."""
    module = _load_game_module_or_404(game)
    require_can_view_game(user, game)
    return module


def _create_context(
    user: User,
    game: str,
    module: GameModule,
    *,
    error: str | None,
    submitted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the create form's context.

    ``submitted`` carries back what the player actually typed after a validation
    error, so one mistyped number does not blank the other six fields. On the
    first render there is nothing submitted and the game's defaults stand alone.
    """
    return {
        "user": user,
        "is_admin": _is_any_admin(user),
        "game_slug": game,
        "game_theme": module.theme(),
        "defaults": _form_defaults(module),
        "submitted": submitted or {},
        "error": error,
    }


def _html_error(
    request: Request,
    user: User,
    game: str,
    *,
    message: str,
    status_code: int = 400,
    submitted: dict[str, Any] | None = None,
):
    module = _load_game_module_or_404(game)
    return templates.TemplateResponse(
        request,
        "matches_user/create_match.html",
        _create_context(user, game, module, error=message, submitted=submitted),
        status_code=status_code,
    )


@router.get("/games/{game}/matches/new", response_class=HTMLResponse)
async def create_match_form(
    game: Annotated[str, Path()],
    request: Request,
    user: Annotated[User, Depends(require_user)],
):
    module = _load_visible_game_module_or_404(game, user)
    return templates.TemplateResponse(
        request,
        "matches_user/create_match.html",
        _create_context(user, game, module, error=None),
    )


@router.post("/games/{game}/matches/new")
async def create_match_submit(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
    scheduled_start: Annotated[str, Form()],
    min_players: Annotated[int | None, Form()] = None,
    max_players: Annotated[int | None, Form()] = None,
    per_turn_deadline_seconds: Annotated[int | None, Form()] = None,
    total_rounds: Annotated[int | None, Form()] = None,
    turns_per_round: Annotated[int | None, Form()] = None,
    wild_ones: Annotated[str | None, Form()] = None,
    dice_per_player: Annotated[int, Form()] = 5,
    mutual_help_mode: Annotated[str | None, Form()] = None,
):
    # 404s a non-admin on an under-construction game, so only an admin reaches
    # the rest of this handler for one.
    module = _load_visible_game_module_or_404(game, user)
    is_platform_admin = user.role == UserRole.ADMIN
    defaults = _form_defaults(module)
    cfg = module.config_defaults()

    # An omitted field means "use this game's default", so a client that posts
    # only name and start still creates the match it used to.
    want_min_players = min_players if min_players is not None else defaults["min_players"]
    want_max_players = max_players if max_players is not None else defaults["max_players"]
    want_deadline = (
        per_turn_deadline_seconds
        if per_turn_deadline_seconds is not None
        else defaults["per_turn_deadline_seconds"]
    )
    want_rounds = total_rounds if total_rounds is not None else defaults["total_rounds"]
    want_turns = (
        turns_per_round if turns_per_round is not None else defaults["turns_per_round"]
    )
    submitted = {
        "name": name,
        "scheduled_start": scheduled_start,
        "min_players": want_min_players,
        "max_players": want_max_players,
        "per_turn_deadline_seconds": want_deadline,
        "total_rounds": want_rounds,
        "turns_per_round": want_turns,
        "dice_per_player": dice_per_player,
        "wild_ones": wild_ones is not None,
    }

    try:
        when = datetime.fromisoformat(scheduled_start.replace("Z", "+00:00"))
    except ValueError:
        return _html_error(
            request,
            user,
            game,
            message="Could not read the start time. Please pick a date and time.",
            submitted=submitted,
        )
    when = ensure_aware(when)
    if when <= datetime.now(timezone.utc):
        return _html_error(
            request,
            user,
            game,
            message="Start time must be in the future.",
            submitted=submitted,
        )

    count_error = player_count_error(
        min_players=want_min_players,
        max_players=want_max_players,
        cfg_min_players=cfg.min_players,
        cfg_max_players=cfg.max_players,
        range_message=f"Player counts must be {cfg.min_players} to {cfg.max_players}.",
        order_message="Min players cannot be greater than max players.",
    )
    if count_error is not None:
        return _html_error(
            request, user, game, message=count_error, submitted=submitted
        )
    if not (_MIN_ROUNDS <= want_rounds <= _MAX_ROUNDS):
        return _html_error(
            request,
            user,
            game,
            message=f"Total rounds must be {_MIN_ROUNDS} to {_MAX_ROUNDS}.",
            submitted=submitted,
        )
    if not (_MIN_TURNS <= want_turns <= _MAX_TURNS):
        return _html_error(
            request,
            user,
            game,
            message=f"Turns per round must be {_MIN_TURNS} to {_MAX_TURNS}.",
            submitted=submitted,
        )
    if not (_MIN_DEADLINE <= want_deadline <= _MAX_DEADLINE):
        return _html_error(
            request,
            user,
            game,
            message=f"Per-turn deadline must be {_MIN_DEADLINE} to {_MAX_DEADLINE} seconds.",
            submitted=submitted,
        )
    # Same bound the JSON API already enforces. A zero-dice match is a wedged
    # match, not a rejected request, so this has to fail before the write — but
    # only on a game that actually has dice.
    if game_owns_match_config(game) and not (_MIN_DICE <= dice_per_player <= _MAX_DICE):
        return _html_error(
            request,
            user,
            game,
            message=f"Dice per player must be {_MIN_DICE} to {_MAX_DICE}.",
            submitted=submitted,
        )

    # Choosing the per-match rule is a platform-admin power. A player's submitted
    # value is ignored rather than rejected: the control isn't rendered for them,
    # so a value here means a hand-made request, not a mistake worth explaining.
    chosen_mode = "decay"
    if is_platform_admin and mutual_help_mode:
        # Reject an unknown mode rather than letting it reach the column: a typo
        # that silently became "decay" would mislabel which rule a match was
        # played under.
        try:
            MutualHelpMode(mutual_help_mode)
        except ValueError:
            return _html_error(
                request,
                user,
                game,
                message=f"Unknown mutual-help mode {mutual_help_mode!r}.",
                submitted=submitted,
            )
        chosen_mode = mutual_help_mode

    if not is_platform_admin:
        active_count = (
            await db.scalar(
                select(func.count())
                .select_from(Match)
                .where(
                    Match.created_by_user_id == user.id,
                    Match.state.in_(
                        [GameState.SCHEDULED, GameState.REGISTERING, GameState.ACTIVE]
                    ),
                )
            )
        ) or 0
        if active_count >= settings.user_active_match_limit:
            return _html_error(
                request,
                user,
                game,
                message=(
                    f"You can have at most {settings.user_active_match_limit} "
                    "active matches at once."
                ),
                status_code=status.HTTP_409_CONFLICT,
                submitted=submitted,
            )

    try:
        await create_match_with_state(
            db,
            game=game,
            name=name,
            scheduled_start=when,
            state=GameState.REGISTERING,
            created_by_user_id=user.id,
            mutual_help_mode=chosen_mode,
            state_config=state_config_for(
                game,
                wild_ones=wild_ones is not None,
                dice_per_player=dice_per_player,
            ),
            min_players=want_min_players,
            max_players=want_max_players,
            per_turn_deadline_seconds=want_deadline,
            total_rounds=want_rounds,
            turns_per_round=want_turns,
        )
    except ValueError as exc:
        return _html_error(request, user, game, message=str(exc), submitted=submitted)

    # An admin creating from the per-game dashboard belongs back on it; a player
    # has no dashboard, so their match is waiting on /me/matches.
    landing = f"/games/{game}/admin" if is_platform_admin else "/me/matches"
    return RedirectResponse(url=landing, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/games/{game}/matches/{match_id}/start")
async def start_match_submit(
    match: GameScopedMatchOr404,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    """Let the only player in a match start it now (filling bots to the floor).

    Shown as the "Start now" button only when ``viewer_start_eligibility`` says
    yes; re-checked here so a stale page can't start a match the user no longer
    solely owns. Bots fill any empty seats up to the start floor so the match can
    actually run, then the match goes ACTIVE. The ``{game}``-slug check is the
    injected ``GameScopedMatchOr404`` dependency (404 "Match not found." on
    mismatch — same body the old inline check returned).
    """
    eligibility = await viewer_start_eligibility(db, match, user)
    if not eligibility.can_start:
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="CANNOT_START",
            message="You can't start this match yet.",
        )
    await start_match_for_user(db, match)
    return RedirectResponse(
        url=f"/games/{match.game}/matches/{match.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/matches/{match_id}/delete")
async def delete_match_submit(
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    match = await _load_match_or_404(db, match_id)
    if user.role != UserRole.ADMIN and match.created_by_user_id != user.id:
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="NOT_MATCH_OWNER",
            message="You can only delete matches you created.",
        )
    if user.role != UserRole.ADMIN and match.state not in (
        GameState.SCHEDULED,
        GameState.REGISTERING,
    ):
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="MATCH_ALREADY_STARTED",
            message="Match already started.",
        )
    await delete_match(db, match.id)
    return RedirectResponse(url="/me/matches", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/matches/{match_id}/cancel")
async def cancel_match_submit(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
):
    # Cancel is an admin-only power (admins are the "organizers"). Regular users
    # cannot cancel — they can only delete their own match before it starts.
    # Cancel preserves data, so it is allowed from any non-terminal state
    # (including ACTIVE); only already-ended matches are rejected.
    match = await _load_match_or_404(db, match_id)
    if match.state in (GameState.COMPLETED, GameState.CANCELLED):
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="MATCH_ALREADY_ENDED",
            message="Match already ended.",
        )
    await cancel_match(db, match)
    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_303_SEE_OTHER)
