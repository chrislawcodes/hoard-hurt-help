"""Guide, runner download, join, and player dashboard web routes."""

import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path as FsPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select

from app.aware_datetime import ensure_aware
from app.config import settings
from app.deps import DbSession, get_current_user, require_user, require_user_with_handle
from app.engine.connection_health import (
    ProviderReadiness,
    provider_readiness,
    providers_busy_for_user,
    user_play_readiness,
)
from app.routes.nav_context import PlaySetupStage, resolve_play_setup_state
from app.engine.scheduler import start_game
from app.engine.seat_hold import SEAT_HOLD_SECONDS, confirm_seat_if_live, hold_deadline
from app.games import get as get_game_module
from app.games import is_admin_only
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.match import Match, GameState, MatchKind
from app.models.player import Player
from app.models.user import User, UserRole
from app.request_logging import set_request_trace_context
from app.routes.connections_connect_guide import _play_prompt
from app.routes.provider_labels import PROVIDER_LABELS
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _load_match_or_404,
    _load_owned_player_match_or_404,
    _player_count,
    _redirect_to_match,
    _redirect_if_game_slug_mismatch,
)
from app.templating import templates

router = APIRouter(tags=["web"])

_DOCS_DIR = FsPath("docs")
_GUIDE_NAME = re.compile(r"^[a-z0-9-]+$")

# On the held-seat page, a "returning" provider (set up on a connection) is told
# to just paste the play-prompt to wake it. If it's still wired into the user's
# AI client, it checks in within seconds. So if it hasn't come online after this
# grace window, the MCP server has almost certainly dropped out of the client —
# pasting won't help, and we escalate the poll to a "reconnect your server" CTA.
# This is the liveness check: we don't trust the DB "configured" flag, we watch
# whether the connection actually comes online and act when it doesn't.
_STALL_WAKE_SECONDS = 45


def _hx_redirect(url: str) -> HTMLResponse:
    """An empty 200 that tells HTMX to navigate the whole page to *url*."""
    return HTMLResponse("", headers={"HX-Redirect": url})


def _seat_name(agent_name: str, existing: set[str]) -> str:
    """Derive a public seat name and keep it unique within the match.

    The seat name shown to agents and spectators is the agent's name only —
    never the owning user's handle, name, or email. Identity must not leak to
    competing agents. The human-facing viewer shows the owner's handle as a
    separate byline, sourced from its own query, not from this label.
    """
    base = agent_name[:40]
    if base not in existing:
        return base
    for index in range(2, 100):
        suffix = f" #{index}"
        max_base = 40 - len(suffix)
        candidate = base[:max_base] + suffix if len(base) > max_base else f"{base}{suffix}"
        if candidate not in existing:
            return candidate
    raise HTTPException(status_code=409, detail="Could not allocate a unique seat name.")


@router.get("/guide/{name}", response_class=HTMLResponse)
async def guide(name: Annotated[str, Path()], request: Request, db: DbSession):
    """Render a setup doc from docs/<name>.md inside the site chrome."""
    if not _GUIDE_NAME.match(name):
        raise HTTPException(404)
    path = _DOCS_DIR / f"{name}.md"
    if not path.is_file():
        raise HTTPException(404)
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        "guide.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "title": name.replace("-", " ").title(),
            "body": path.read_text(encoding="utf-8"),
        },
    )


# Chained-session setup file download. ONE script drives every CLI provider for a
# connection: agentludum_connector.py. Allowlisted by exact filename below; the
# path never comes from the request, so there is no traversal surface.
_UNIFIED_RUNNER = FsPath("scripts/agentludum_connector.py")
_AGENT_RUNNERS: dict[str, FsPath] = {
    "agentludum_connector.py": _UNIFIED_RUNNER,
}


def _serve_agent_file(name: str) -> FileResponse:
    path = _AGENT_RUNNERS.get(name)
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/x-python", filename=name)


@router.get("/setup-files/{name}", include_in_schema=False)
async def agent_setup_file(name: Annotated[str, Path()]) -> FileResponse:
    """Serve a setup script so the setup `curl` fetches it.

    Allowlisted by exact filename — the path never comes from the request, so
    there's no traversal surface. Single source of truth: this streams the
    repo's scripts/<name>, so the downloaded file always matches this server.
    """
    return _serve_agent_file(name)


@router.get("/runners/{name}", include_in_schema=False)
async def agent_runner_script(name: Annotated[str, Path()]) -> FileResponse:
    return _serve_agent_file(name)


@router.get("/games/{match_id}/join", response_class=HTMLResponse)
async def legacy_join_form_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/join")


@router.post("/games/{match_id}/join", include_in_schema=False)
async def legacy_join_submit_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return RedirectResponse(
        url=(await _redirect_to_match(db, match_id, suffix="/join")).headers["location"],
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )


async def _load_user_agents(
    db: DbSession, user_id: int
) -> list[tuple[Agent, AgentVersion | None]]:
    rows = (
        await db.execute(
            select(Agent, AgentVersion)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(Agent.user_id == user_id, Agent.archived_at.is_(None))
            .order_by(Agent.created_at.desc(), Agent.id.desc())
        )
    ).all()
    return [(agent, version) for agent, version in rows]


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
    options: list[dict[str, object]] = []
    for value, label in PROVIDER_LABELS.items():
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


async def _seat_provider_readiness(
    db: DbSession, user_id: int, player: Player
) -> ProviderReadiness:
    """Readiness of the AI a seat was joined with (its chosen provider).

    Legacy seats with no chosen provider fall back to the user's best readiness.
    """
    if player.chosen_provider:
        return await provider_readiness(
            db, user_id, ConnectionProvider(player.chosen_provider)
        )
    return await user_play_readiness(db, user_id)


def _seat_provider_label(player: Player) -> str:
    """Friendly name of the AI a seat was joined with."""
    if player.chosen_provider:
        return PROVIDER_LABELS.get(player.chosen_provider, player.chosen_provider.title())
    return "your AI"


async def _join_setup_redirect(
    db: DbSession, user: User, match: Match
) -> RedirectResponse | None:
    """Send the user to the FIRST setup step they're missing, or None to render
    the join form.

    Uses the shared play-setup resolver with ``require=NEEDS_AGENT``: once the
    user has an eligible AI agent (regardless of whether the provider is live
    right now), the resolver returns READY and we render the join form. The join
    form itself shows provider liveness status and handles the held-seat connect
    flow for agents whose provider is offline.

    ``target_match`` is NOT passed to the resolver so the stage-only URL is
    returned; we then append a URL-encoded ``?next=`` pointing back to the match's
    join URL. This preserves the URL-encoding contract that downstream pages and
    tests rely on.
    """
    state = await resolve_play_setup_state(
        db, user, require=PlaySetupStage.NEEDS_AGENT
    )
    if state.stage == PlaySetupStage.READY:
        return None
    join_url = f"/games/{match.game}/matches/{match.id}/join"
    next_param = quote(join_url, safe="")
    separator = "&" if "?" in state.next_url else "?"
    redirect_url = f"{state.next_url}{separator}next={next_param}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


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

    # Smart hub: if the operator is missing setup, walk them through ONLY the
    # missing step on the existing pages, carrying ?next back to this join URL.
    # Returns None once they have a seatable AI agent — then we render the
    # join form below. No Player is seated here; backing out leaves no half-join.
    if redirect := await _join_setup_redirect(db, user, match):
        return redirect

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
    # (ready / connected-not-playing / not-connected / busy-in-another-game).
    busy = await providers_busy_for_user(db, user.id)
    ai_options = await _build_ai_options(db, user.id, busy)
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
            "any_pickable_ai": any(o["can_pick"] for o in ai_options),
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
    # One AI = one game: refuse a provider already committed to another not-finished
    # game (admins may overcommit for testing).
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
    agent_id: Annotated[list[int] | None, Form()] = None,
    bot_id: Annotated[list[int] | None, Form()] = None,
    chosen_provider: Annotated[str | None, Form()] = None,
    display_name: Annotated[str | None, Form()] = None,
    strategy_prompt: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Enter one or more of the user's agents into a game.

    Regular users add a single agent. Admins may select several of their own
    agents at once to fill a match for testing — the backend already allows one
    user to field multiple (distinct) agents; this just lets them do it in one
    submit instead of re-running the join flow per agent.
    """
    # Dedupe while preserving the picked order; `bot_id` is the legacy field name.
    selected_ids = list(dict.fromkeys([*(agent_id or []), *(bot_id or [])]))
    if not selected_ids:
        raise HTTPException(status_code=400, detail="Choose an agent.")
    if not chosen_provider:
        raise HTTPException(status_code=400, detail="Pick an AI to play your agent.")
    is_admin = _is_any_admin(user)
    if len(selected_ids) > 1 and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only admins can add more than one agent to a match.",
        )
    set_request_trace_context(
        request,
        match_id=match_id,
        stage="join_submit",
        agent_id=selected_ids[0],
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

    existing_seats = set(
        (
            await db.execute(select(Player.seat_name).where(Player.match_id == match.id))
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


@router.get(
    "/games/{game}/matches/{match_id}/connect/{player_id}",
    response_class=HTMLResponse,
)
async def seat_connect(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    player_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    """Post-join 'one step left' page: the seat-hold countdown + connect the AI.

    Shown right after joining with an agent whose provider wasn't live. It counts
    down the hold window and offers a Connect button; an HTMX poll auto-locks the
    seat (and navigates to the match) the moment the AI comes online.
    """
    player, match = await _load_owned_player_match_or_404(
        db, player_id, user.id, missing_detail="Seat not found."
    )
    match_url = f"/games/{match.game}/matches/{match.id}"
    if player.seat_reserved_until is None:
        # Already confirmed (or never held) — nothing to wait for.
        return RedirectResponse(url=match_url, status_code=status.HTTP_303_SEE_OTHER)

    provider_label = _seat_provider_label(player)
    if await _seat_provider_readiness(db, user.id, player) in (
        ProviderReadiness.NO_MCP_CONNECTION,
        ProviderReadiness.CONNECTED_NOT_LIVE,
    ):
        # That AI has no *active* connection: either nothing set up, or set up but
        # not seen within LIVE_WINDOW_SECONDS. An inactive connection needs
        # reconnect, not "start polling" — send it to /me/connections (scoped to
        # the pick). Only SEEN_NOT_POLLING (active but not looping yet) belongs on
        # the wait page below.
        next_q = quote(f"{match_url}/connect/{player.id}", safe="")
        provider_q = f"provider={player.chosen_provider}&" if player.chosen_provider else ""
        return RedirectResponse(
            url=f"/me/connections?{provider_q}next={next_q}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    status_url = f"{match_url}/connect/{player.id}/status"
    return templates.TemplateResponse(
        request,
        "seat_connect.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game": match,
            "game_theme": _game_theme(match),
            "player": player,
            "provider_label": provider_label,
            "play_prompt": _play_prompt(),
            "status_url": status_url,
        },
    )


@router.get(
    "/games/{game}/matches/{match_id}/connect/{player_id}/status",
    response_class=HTMLResponse,
)
async def seat_connect_status(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    player_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    """HTMX poll for the connect countdown.

    Confirms the seat on the spot if the AI just came online (and HX-redirects to
    the match), releases it if the deadline passed, else keeps the user waiting.
    """
    row = (
        await db.execute(
            select(Player, Match)
            .join(Match, Match.id == Player.match_id)
            .where(Player.id == player_id, Player.user_id == user.id)
        )
    ).one_or_none()
    if row is None:
        # Seat already released (row deleted) — show how to get back in.
        return templates.TemplateResponse(
            request,
            "fragments/seat_connect_status.html",
            {
                "state": "released",
                "provider_label": "your AI",
                "join_url": f"/games/{game}/matches/{match_id}/join",
            },
        )
    player, match = row
    match_url = f"/games/{match.game}/matches/{match.id}"
    if player.seat_reserved_until is None:
        return _hx_redirect(match_url)

    provider_label = _seat_provider_label(player)
    # Confirm on the spot if the chosen AI just came online — don't wait for the
    # background poller, so the page reacts the instant it connects.
    if await confirm_seat_if_live(db, player):
        await db.commit()
        return _hx_redirect(match_url)

    now = datetime.now(timezone.utc)
    if ensure_aware(player.seat_reserved_until) <= now:
        # Deadline passed and still not live — release the seat now.
        await db.delete(player)
        await db.commit()
        return templates.TemplateResponse(
            request,
            "fragments/seat_connect_status.html",
            {
                "state": "released",
                "provider_label": provider_label,
                "join_url": f"/games/{match.game}/matches/{match.id}/join",
            },
        )
    # Liveness detection: the seat is still held and the provider hasn't come
    # online. If it was set up on a connection (a "returning" provider) yet still
    # hasn't checked in after the grace window, the MCP server has dropped out of
    # the user's AI client — waking it won't work until they reconnect it. Surface
    # a prominent reconnect CTA. The poll keeps running underneath, so the moment
    # they reconnect and it comes online we still auto-seat them.
    deadline = ensure_aware(player.seat_reserved_until)
    waited_seconds = SEAT_HOLD_SECONDS - (deadline - now).total_seconds()
    is_configured = (
        await _seat_provider_readiness(db, user.id, player)
        != ProviderReadiness.NO_MCP_CONNECTION
    )
    escalate = is_configured and waited_seconds >= _STALL_WAKE_SECONDS
    reconnect_url = None
    if escalate:
        connect_next = quote(f"{match_url}/connect/{player.id}", safe="")
        provider_q = f"provider={player.chosen_provider}&" if player.chosen_provider else ""
        reconnect_url = f"/me/connections?{provider_q}next={connect_next}"
    return templates.TemplateResponse(
        request,
        "fragments/seat_connect_status.html",
        {
            "state": "held",
            "provider_label": provider_label,
            "status_url": f"{match_url}/connect/{player.id}/status",
            "escalate": escalate,
            "reconnect_url": reconnect_url,
        },
    )


@router.get("/me/matches", response_class=HTMLResponse)
async def my_matches(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    players = (
        (await db.execute(select(Player).where(Player.user_id == user.id))).scalars().all()
    )
    owned_matches = (
        (
            await db.execute(
                select(Match)
                .where(Match.created_by_user_id == user.id)
                .order_by(Match.scheduled_start.desc(), Match.id.desc())
            )
        )
        .scalars()
        .all()
    )
    match_ids = {p.match_id for p in players}
    match_ids.update(m.id for m in owned_matches)
    if not match_ids:
        return templates.TemplateResponse(
            request,
            "my_matches.html",
            {"user": user, "is_admin": _is_any_admin(user), "game_sections": []},
        )
    match_id_list = list(match_ids)

    matches = {
        m.id: m
        for m in (await db.execute(select(Match).where(Match.id.in_(match_id_list)))).scalars().all()
    }

    own_seats_by_match: dict[str, list[str]] = {}
    for p in players:
        own_seats_by_match.setdefault(p.match_id, []).append(p.seat_name)

    count_rows = (await db.execute(
        select(
            Player.match_id,
            func.count(Player.id).label("total"),
            func.sum(case((Agent.kind == AgentKind.BOT, 1), else_=0)).label("bot_count"),
        )
        .join(Agent, Agent.id == Player.agent_id)
        .where(Player.match_id.in_(match_id_list))
        .group_by(Player.match_id)
    )).all()
    counts_by_match = {row.match_id: row for row in count_rows}

    sections_map: dict[str, dict] = {}
    ordered_matches = sorted(
        matches.values(), key=lambda g: (g.scheduled_start, g.id), reverse=True
    )
    for g in ordered_matches:
        slug = g.game
        if slug not in sections_map:
            title = {"hoard-hurt-help": "Hoard Hurt Help"}.get(slug, slug.replace("-", " ").title())
            sections_map[slug] = {"title": title, "active": [], "completed": [], "cancelled": []}

        row = counts_by_match.get(g.id)
        total = int(row.total or 0) if row else 0
        bot_count = int(row.bot_count or 0) if row else 0
        agent_count = total - bot_count
        parts: list[str] = []
        if agent_count:
            parts.append(f"{agent_count} {'agent' if agent_count == 1 else 'agents'}")
        if bot_count:
            parts.append(f"{bot_count} {'bot' if bot_count == 1 else 'bots'}")
        players_label = ", ".join(parts) if parts else "0 players"
        activity_bits: list[str] = []
        own_seats = sorted(own_seats_by_match.get(g.id, []))
        if own_seats:
            activity_bits.append(f"Playing as {', '.join(own_seats)}")
        if g.created_by_user_id == user.id:
            activity_bits.append("Created by you")

        entry = {
            "id": g.id,
            "name": g.name,
            "state": g.state,
            "watch_url": f"/games/{g.game}/matches/{g.id}",
            "activity_label": " · ".join(activity_bits),
            "players_label": players_label,
            "can_delete": user.role == UserRole.ADMIN
            or (g.created_by_user_id == user.id and g.state in (GameState.SCHEDULED, GameState.REGISTERING)),
            "delete_url": f"/matches/{g.id}/delete",
        }
        if g.state == GameState.COMPLETED:
            sections_map[slug]["completed"].append(entry)
        elif g.state == GameState.CANCELLED:
            sections_map[slug]["cancelled"].append(entry)
        else:
            sections_map[slug]["active"].append(entry)

    return templates.TemplateResponse(
        request,
        "my_matches.html",
        {"user": user, "is_admin": _is_any_admin(user), "game_sections": list(sections_map.values())},
    )


@router.get("/me/games", response_class=HTMLResponse, include_in_schema=False)
async def my_games_redirect(request: Request):
    return RedirectResponse(url="/me/matches", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/me/players/{player_id}", response_class=HTMLResponse)
async def player_dashboard(
    player_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    saved: bool = False,
):
    player, game = await _load_owned_player_match_or_404(
        db,
        player_id,
        user.id,
        missing_detail="Agent slot not found.",
    )
    presets = [asdict(p) for p in get_game_module(game.game).strategy_presets()]

    agent_row = (
        await db.execute(
            select(Agent, AgentVersion)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(Agent.id == player.agent_id)
        )
    ).one_or_none()
    current_agent: Agent | None = None
    current_version: AgentVersion | None = None
    if agent_row is not None:
        current_agent, current_version = agent_row

    fresh_key: str | None = None

    selected_ai = request.session.pop(f"ai_type_{player.id}", None)
    pre_game = game.state in (GameState.SCHEDULED, GameState.REGISTERING)

    return templates.TemplateResponse(
        request,
        "connection.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game": game,
            "game_theme": _game_theme(game),
            "player": player,
            "agent": current_agent,
            "version": current_version,
            "agent_key": fresh_key,
            "strategy": current_version.strategy_text if current_version else "",
            "base_url": settings.base_url,
            "selected_ai": selected_ai,
            "presets": presets,
            "just_saved": saved,
            "can_edit_strategy": False,
            "can_leave": pre_game,
            "pre_game": pre_game,
        },
    )


@router.post("/me/players/{player_id}/leave")
async def web_leave(
    player_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    player, game = await _load_owned_player_match_or_404(db, player_id, user.id)
    if game.state not in (GameState.SCHEDULED, GameState.REGISTERING):
        raise HTTPException(409, detail="Cannot leave after start.")
    player.left_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/me/matches", status_code=status.HTTP_303_SEE_OTHER)
