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
from app.engine.model_provider_match import provider_for_model
from app.engine.model_verification import model_status_for
from app.engine.scheduler import start_game
from app.engine.seat_hold import hold_deadline
from app.models.model_verification import ModelVerificationStatus
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.models.user import User
from app.provider_labels import PROVIDER_LABELS
from app.request_logging import set_request_trace_context
from app.routes.web_play import seat_human_player
from app.routes.web_player_shared import (
    _load_user_agents,
    _seat_name,
    _seat_provider_readiness,
)
from app.routes.web_match_loaders import (
    GameScopedMatchPost,
    _load_match_or_404,
    raise_for_game_slug_mismatch,
)
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _player_count,
    require_can_view_game,
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


async def _default_human_choice(db: DbSession, user_id: int) -> bool:
    """Should "Play manually" start ticked? True when this user's newest seat was
    a human one.

    A brand-new user (no history) defaults to True — the no-setup path. Note the
    fallback is deliberately NOT "true when nothing else is selected": the agent
    rows now start unticked by design, so an unconditional fallback would pre-tick
    the manual row for everyone and let one click accidentally seat a human player.
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
        return True
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
    return AgentKind.HUMAN in last_kinds


async def _build_agent_rows(
    db: DbSession, user: User, match: Match
) -> list[dict[str, object]]:
    """The user's AI agents for the join picker, each flagged if it's already
    seated in this match or if its preferred model is verified-failing.

    Agents are per-game, so only this match's game shows (the filter lives here,
    not in the shared ``_load_user_agents``, which the connect flows also use).
    FR-014: warn (not block) when the agent's preferred model is
    verified-failing on every live machine connection for its provider. A
    not-yet-checked model doesn't warn. No Player is seated here — this is a
    read-only picker.
    """
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
    pickable = [
        (agent, version)
        for agent, version in agents
        if agent.kind == AgentKind.AI and version is not None and agent.game == match.game
    ]
    agent_rows: list[dict[str, object]] = []
    for agent, version in pickable:
        preferred_failing = False
        if agent.preferred_model:
            prov = provider_for_model(agent.preferred_model)
            if prov:
                preferred_failing = (
                    await model_status_for(db, user.id, prov, agent.preferred_model)
                ) is ModelVerificationStatus.FAILED
        agent_rows.append(
            {
                "agent": agent,
                "version": version,
                "seated": agent.id in seated_agent_ids,
                "preferred_model_failing": preferred_failing,
            }
        )
    return agent_rows


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
    # The sign-in / handle redirects above must run first (they bounce a signed-out
    # or handle-less visitor), so this route can't take the slug check as a
    # signature dependency — those resolve before the body. Call the shared check
    # here instead: same logic and same 301 target as the dependency form.
    match = await _load_match_or_404(db, match_id)
    raise_for_game_slug_mismatch(match, game, suffix="/join")
    require_can_view_game(user, match.game)

    # No setup gate here: the join screen always renders (given sign-in + handle
    # above) with "Play manually" as the first, pre-selected choice, so a brand-
    # new user with no AI can play as a human in one click. The AI-agent picker
    # below is the opt-in path; picking an agent whose provider is offline routes
    # to the held-seat connect flow on submit. No Player is seated on GET.
    join_url = f"/games/{game}/matches/{match_id}/join"
    agent_rows = await _build_agent_rows(db, user, match)
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

    default_human = await _default_human_choice(db, user.id)
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
            # Remember whether this user last played by hand, so the manual row
            # starts in the same state. Agent rows always start unticked.
            "default_human": default_human,
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


def _pair_agents_with_providers(
    selected_ids: list[int],
    chosen_provider: list[str] | None,
    *,
    is_admin: bool,
) -> list[tuple[int, str]]:
    """Pair each chosen agent with the AI that will play it (pure validation).

    The screen posts one provider per chosen agent, in card order, so the lists
    line up by position. A single provider for several agents is the legacy admin
    "same AI for all" shorthand. One AI plays one of your seats per game: a regular
    user must give a different AI per agent; admins may overcommit. Raises
    HTTPException on any mismatch. Does not touch the database.
    """
    providers = chosen_provider or []
    if not providers:
        raise HTTPException(status_code=400, detail="Pick an AI to play your agent.")
    if len(providers) == len(selected_ids):
        pairs = list(zip(selected_ids, providers))
    elif len(providers) == 1:
        pairs = [(aid, providers[0]) for aid in selected_ids]
    else:
        raise HTTPException(status_code=400, detail="Each agent needs exactly one AI.")
    if not is_admin:
        picked = [provider for _, provider in pairs]
        if len(set(picked)) != len(picked):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Pick a different AI for each agent — one AI can only play "
                    "one of your agents per game."
                ),
            )
    return pairs


async def _seat_agent_players(
    db: DbSession,
    user: User,
    match: Match,
    pairs: list[tuple[int, str]],
    *,
    is_admin: bool,
) -> list[Player]:
    """Build and stage the AI-agent Player rows for *pairs* (no commit).

    Reads existing seat names, runs each agent through ``_seat_user_agent`` (which
    validates ownership/capacity and derives a unique seat name), and adds the rows
    to the session. The caller owns the transaction: staging here so the human seat
    seated afterward counts these rows via autoflush is deliberate — do not commit.
    """
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
            chosen_provider=provider, bypass_capacity=is_admin,
        )
        for aid, provider in pairs
    ]
    db.add_all(players)
    return players


async def _post_join_redirect(
    db: DbSession, user: User, match: Match, held: list[Player]
) -> RedirectResponse:
    """Where to send the user after seats are committed (read-only routing).

    With no held seat, land on the match page. With a held seat waiting on its
    chosen AI, send the user to the connect countdown for the first held seat —
    or, if that AI isn't connected yet, to the connection setup scoped to the pick.
    """
    if held:
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


@router.post("/games/{game}/matches/{match_id}/join")
async def join_submit(
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    match: GameScopedMatchPost,
    play_as: Annotated[str, Form()] = "ai",
    agent_id: Annotated[list[int] | None, Form()] = None,
    bot_id: Annotated[list[int] | None, Form()] = None,
    chosen_provider: Annotated[list[str] | None, Form()] = None,
    display_name: Annotated[str | None, Form()] = None,
    strategy_prompt: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Enter a match: play as a human, send AI agent(s), or both at once.

    The join screen posts independent intents:

    - ``play_as="human"`` adds a **human seat** (no agent, no connection) — shared
      with the direct ``/play/join`` endpoint via ``seat_human_player`` so the two
      can't drift.
    - one or more ``agent_id`` + a matching ``chosen_provider`` adds **AI-agent
      seat(s)**. The browser posts one provider per chosen agent, in the same order
      (paired by position). A single provider given for several agents is the
      legacy "same AI for all" shorthand and is broadcast to every agent.

    Either, or **both**, may be present — a user can play by hand *and* field their
    own bot in the same match (and compete against it). A regular user may field
    several agents at once, but each must use a **different AI** (one AI plays one
    of your seats per game); admins may overcommit one AI across seats for testing.
    All seats are created in **one transaction**, so capacity is all-or-nothing.
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
    # `match` is injected by GameScopedMatchPost: it loads the match (404 if
    # missing) and, on a {game}-slug mismatch, raises the 308 redirect to the
    # canonical /join URL (308 keeps the POST method). `user` is listed before
    # `match` in the signature so a handle-less visitor still 303s to /me/handle
    # before the slug check, exactly as the old inline order did.
    require_can_view_game(user, match.game)
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
        pairs = _pair_agents_with_providers(
            selected_ids, chosen_provider, is_admin=is_admin
        )
        players = await _seat_agent_players(db, user, match, pairs, is_admin=is_admin)

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

    return await _post_join_redirect(db, user, match, held)
