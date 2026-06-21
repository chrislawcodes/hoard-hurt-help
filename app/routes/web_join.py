"""The join flow: the AI picker, the join screen, and the join submit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession, get_current_user, require_user_with_handle
from app.engine.connection_health import (
    ProviderReadiness,
    provider_readiness,
    providers_busy_for_user,
)
from app.engine.scheduler import start_game
from app.engine.seat_hold import hold_deadline
from app.games import is_admin_only
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.models.user import User
from app.request_logging import set_request_trace_context
from app.routes.provider_labels import PROVIDER_LABELS
from app.routes.web_play import seat_human_player
from app.routes.web_player_shared import (
    _load_user_agents,
    _seat_name,
    _seat_provider_readiness,
)
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _load_match_or_404,
    _player_count,
    _redirect_if_game_slug_mismatch,
)
from app.templating import templates

router = APIRouter(tags=["web"])


# Order AIs in the join picker: ready first, then connected-but-idle, then
# not-connected (set up next), with busy ones last (can't be picked).
_AI_STATE_RANK = {"ready": 0, "idle": 1, "not_connected": 2, "busy": 3}


async def _build_ai_options(
    db: DbSession, user_id: int, busy: dict[str, str]
) -> list[dict[str, object]]:
    """The "which AI plays it?" picker: every supported provider with its state.

    States: ``ready`` (live, plays now), ``idle`` (connected but not running yet),
    ``not_connected`` (no MCP connection — picking routes to set it up), and
    ``busy`` (already in another game — shown but not pickable).
    """
    _ACTIVE = {"claude", "gemini", "openai"}
    options: list[dict[str, object]] = []
    for value, label in PROVIDER_LABELS.items():
        if value not in _ACTIVE:
            continue
        if value in busy:
            options.append(
                {
                    "provider": value,
                    "label": label,
                    "state": "busy",
                    "busy_match": busy[value],
                    "can_pick": False,
                }
            )
            continue
        readiness = await provider_readiness(db, user_id, ConnectionProvider(value))
        if readiness == ProviderReadiness.LIVE:
            state = "ready"
        elif readiness in (
            ProviderReadiness.SEEN_NOT_POLLING,
            ProviderReadiness.CONNECTED_NOT_LIVE,
        ):
            state = "idle"
        else:
            state = "not_connected"
        options.append(
            {
                "provider": value,
                "label": label,
                "state": state,
                "busy_match": None,
                "can_pick": True,
            }
        )
    options.sort(key=lambda o: (_AI_STATE_RANK[str(o["state"])], str(o["label"])))
    return options


async def _default_entry_choice(
    db: DbSession, user_id: int, *, agent_pickable: bool
) -> tuple[bool, bool]:
    """Pre-check the join boxes to match how this user last entered a match.

    Looks at the user's most recent match (their newest seat) and whether, in that
    match, they held a human seat, an AI-agent seat, or both — then returns
    ``(default_human, default_agent)`` to start the form in the same shape. A
    brand-new user (no history) defaults to the human seat, the no-setup path.

    The agent box can only default on when there's actually a pickable AI agent
    right now; if the remembered choice was agent-only but none is pickable, fall
    back to the human box so the form never starts with nothing selected.
    """
    last_match_id = (
        await db.execute(
            select(Player.match_id)
            .where(Player.user_id == user_id)
            .order_by(Player.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_match_id is None:
        return True, False
    last_kinds = set(
        (
            await db.execute(
                select(Agent.kind)
                .join(Player, Player.agent_id == Agent.id)
                .where(Player.user_id == user_id, Player.match_id == last_match_id)
            )
        )
        .scalars()
        .all()
    )
    default_human = AgentKind.HUMAN in last_kinds
    default_agent = AgentKind.AI in last_kinds and agent_pickable
    if not default_human and not default_agent:
        default_human = True
    return default_human, default_agent


@router.get("/games/{game}/matches/{match_id}/join", response_class=HTMLResponse)
async def join_form(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    user = await get_current_user(request, db)
    if user is None:
        # Send through OAuth, returning back to this URL.
        return RedirectResponse(
            url=f"/auth/google/login?next=/games/{game}/matches/{match_id}/join",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if user.handle is None:
        # A handle is required to enter a match — pick one, then come back here.
        target = quote(f"/games/{game}/matches/{match_id}/join", safe="")
        return RedirectResponse(
            url=f"/me/handle?next={target}", status_code=status.HTTP_303_SEE_OTHER
        )

    set_request_trace_context(request, match_id=match_id, stage="join_form")
    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(match, game, "/join"):
        return redirect
    if is_admin_only(match.game) and not _is_any_admin(user):
        raise HTTPException(status_code=404, detail="Game not found.")

    # No setup gate here: the join screen always renders (given sign-in + handle
    # above) with "Play as yourself" as the first, pre-selected choice, so a brand-
    # new user with no AI can play as a human in one click. The AI-agent picker
    # below is the opt-in path; picking an agent whose provider is offline routes
    # to the held-seat connect flow on submit. No Player is seated on GET.
    join_url = f"/games/{game}/matches/{match_id}/join"
    agents = await _load_user_agents(db, user.id)
    # Agents already seated in this match stay visible, but they can't join
    # again. The admin multi-seat flow still uses the same list.
    seated_agent_ids = set(
        (
            await db.execute(
                select(Player.agent_id).where(
                    Player.match_id == match.id, Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    agent_rows = [
        {
            "agent": agent,
            "version": version,
            "seated": agent.id in seated_agent_ids,
        }
        for agent, version in agents
        if agent.kind == AgentKind.AI and version is not None
    ]
    # The "which AI plays it?" picker: each supported AI with its state
    # (ready / connected-not-playing / not-connected / busy-in-a-game). One AI
    # plays one seat at a time, so an AI already in any unfinished game is busy.
    busy = await providers_busy_for_user(db, user.id)
    ai_options = await _build_ai_options(db, user.id, busy)
    any_pickable_ai = any(o["can_pick"] for o in ai_options)
    # An AI counts as "connected" once it's live or set-up-but-idle. When the user
    # has at least one, the picker shows only those and tucks the rest behind a
    # "connect another AI" link — so a set-up operator isn't wading through four
    # "not connected" chips. With none connected, every provider stays visible so
    # a cold-start user can pick one and be routed to set it up.
    any_connected_ai = any(o["state"] in ("ready", "idle") for o in ai_options)

    default_human, default_agent = await _default_entry_choice(
        db, user.id, agent_pickable=bool(agent_rows) and any_pickable_ai
    )
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game": match,
            "game_theme": _game_theme(match),
            "player_count": await _player_count(db, match.id),
            "agent_rows": agent_rows,
            "ai_options": ai_options,
            "any_agents": bool(agent_rows),
            "any_pickable_ai": any_pickable_ai,
            "any_connected_ai": any_connected_ai,
            # Remember how this user last entered a match so the boxes start there.
            "default_human": default_human,
            "default_agent": default_agent,
            # The "create another agent" CTA carries ?next back to this join page.
            "join_url": join_url,
            "base_url": settings.base_url,
            "error": None,
        },
    )


async def _seat_user_agent(
    db: DbSession,
    user: User,
    match: Match,
    agent_id: int,
    existing_seats: set[str],
    *,
    chosen_provider: str,
    bypass_capacity: bool = False,
) -> Player:
    """Validate one of *user*'s agents + chosen AI and build its Player row.

    The user picks which AI plays the seat (*chosen_provider*). We record it so
    routing only lets a connection covering that provider serve the seat. Runs the
    gate (ownership, valid provider, "one AI = one game", not-already-seated) and
    derives a unique seat name. Mutates *existing_seats*. Does not commit — the
    caller owns the transaction. Raises HTTPException on any problem, naming the
    agent.

    *bypass_capacity* lets an admin reuse an AI that's already in another game
    (the "one AI = one game" rule is a guard against timeouts, not a hard lock —
    admins testing want to overcommit on purpose).
    """
    if chosen_provider not in PROVIDER_LABELS:
        raise HTTPException(status_code=400, detail="Pick an AI to play this agent.")
    agent_row = (
        await db.execute(
            select(Agent, AgentVersion)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(
                Agent.id == agent_id,
                Agent.user_id == user.id,
                Agent.kind == AgentKind.AI,
                Agent.archived_at.is_(None),
            )
        )
    ).one_or_none()
    if agent_row is None:
        raise HTTPException(404, detail="Agent not found.")
    selected_agent, version = agent_row
    if version is None:
        raise HTTPException(
            status_code=409, detail=f"{selected_agent.name} has no current version."
        )
    provider_label = PROVIDER_LABELS[chosen_provider]
    # Re-joining the same agent is the clearest error, so check it first.
    already_in = (
        await db.execute(
            select(Player.id).where(
                Player.agent_id == selected_agent.id,
                Player.match_id == match.id,
                Player.left_at.is_(None),
            )
        )
    ).first()
    if already_in is not None:
        raise HTTPException(
            status_code=409, detail=f"{selected_agent.name} is already in this game."
        )
    # One AI = one seat at a time: refuse a provider already chosen for any of the
    # user's unfinished seats (admins may overcommit for testing). To field several
    # agents in one game, pick a different AI for each.
    if not bypass_capacity:
        busy = await providers_busy_for_user(db, user.id)
        if chosen_provider in busy:
            raise HTTPException(
                status_code=409,
                detail=f"{provider_label} is already in a game (“{busy[chosen_provider]}”).",
            )
    # Confirm the seat only when the chosen AI is actually running its play loop
    # (not merely seen recently). Otherwise hold the seat and let the next screen
    # walk the user through starting that AI.
    readiness = await provider_readiness(db, user.id, ConnectionProvider(chosen_provider))
    reserved_until: datetime | None = None
    if readiness != ProviderReadiness.LIVE:
        reserved_until = hold_deadline(datetime.now(timezone.utc))
    seat_name = _seat_name(selected_agent.name, existing_seats)
    existing_seats.add(seat_name)
    return Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=selected_agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        chosen_provider=chosen_provider,
        model_self_report=None,
        seat_reserved_until=reserved_until,
    )


@router.post("/games/{game}/matches/{match_id}/join")
async def join_submit(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    play_as: Annotated[str, Form()] = "ai",
    agent_id: Annotated[list[int] | None, Form()] = None,
    bot_id: Annotated[list[int] | None, Form()] = None,
    chosen_provider: Annotated[str | None, Form()] = None,
    display_name: Annotated[str | None, Form()] = None,
    strategy_prompt: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Enter a match: play as a human, send AI agent(s), or both at once.

    The join screen posts independent intents:

    - ``play_as="human"`` adds a **human seat** (no agent, no connection) — shared
      with the direct ``/play/join`` endpoint via ``seat_human_player`` so the two
      can't drift.
    - one or more ``agent_id`` + a ``chosen_provider`` adds **AI-agent seat(s)**.

    Either, or **both**, may be present — a user can play by hand *and* field their
    own bot in the same match (and compete against it). Regular users add a single
    agent; admins may select several of their own at once to fill a match for
    testing. Both seats are created in **one transaction**, so capacity is
    all-or-nothing.
    """
    # Dedupe while preserving the picked order; `bot_id` is the legacy field name.
    selected_ids = list(dict.fromkeys([*(agent_id or []), *(bot_id or [])]))
    is_admin = _is_any_admin(user)
    want_human = play_as == "human"
    want_agent = bool(selected_ids)
    set_request_trace_context(
        request,
        match_id=match_id,
        stage="join_submit",
        agent_id=selected_ids[0] if selected_ids else None,
    )
    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(
        match,
        game,
        "/join",
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    ):
        return redirect
    if is_admin_only(match.game) and not is_admin:
        raise HTTPException(status_code=404, detail="Game not found.")
    if match.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Match not open for registration.")

    if not want_human and not want_agent:
        raise HTTPException(
            status_code=400,
            detail="Choose how to enter — play as yourself, send an agent, or both.",
        )

    # Build the AI-agent seat(s) FIRST, then the human seat. Seating the human
    # last means its capacity check (`seat_human_player` → `count_players`) also
    # counts the agent rows just added (SQLAlchemy autoflush), so a human + an
    # agent that together overflow `max_players` fail as one — nothing commits.
    players: list[Player] = []
    if want_agent:
        if not chosen_provider:
            raise HTTPException(
                status_code=400, detail="Pick an AI to play your agent."
            )
        if len(selected_ids) > 1 and not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only admins can add more than one agent to a match.",
            )
        existing_seats = set(
            (
                await db.execute(
                    select(Player.seat_name).where(Player.match_id == match.id)
                )
            )
            .scalars()
            .all()
        )
        players = [
            await _seat_user_agent(
                db, user, match, aid, existing_seats,
                chosen_provider=chosen_provider, bypass_capacity=is_admin,
            )
            for aid in selected_ids
        ]
        db.add_all(players)

    # A one-click human seat: no agent, no connection. Idempotent (a no-op if the
    # user already holds an active human seat here) and active immediately.
    if want_human:
        await seat_human_player(db, user, match, display_name)

    await db.commit()

    held = [p for p in players if p.seat_reserved_until is not None]

    # Practice arena starts the moment you join — but only if the seat is live.
    # A held seat would just be released at start, so don't auto-start on one.
    if match.match_kind == MatchKind.PRACTICE_ARENA.value and not held:
        await start_game(db, match)

    if held:
        # At least one seat is waiting on its chosen AI. Send the user to the
        # connect countdown for the first held seat so they can bring it online.
        held_player = held[0]
        held_connect_url = f"/games/{match.game}/matches/{match.id}/connect/{held_player.id}"
        if (
            await _seat_provider_readiness(db, user.id, held_player)
            == ProviderReadiness.NO_MCP_CONNECTION
        ):
            # That AI isn't connected yet — set it up first (scoped to the pick).
            next_q = quote(held_connect_url, safe="")
            provider_q = (
                f"provider={held_player.chosen_provider}&" if held_player.chosen_provider else ""
            )
            return RedirectResponse(
                url=f"/me/connections?{provider_q}next={next_q}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return RedirectResponse(
            url=held_connect_url,
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/games/{match.game}/matches/{match.id}", status_code=status.HTTP_303_SEE_OTHER
    )
