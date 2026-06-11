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

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, get_current_user, require_user, require_user_with_handle
from app.engine.connection_health import (
    active_matches_for_provider,
    is_join_blocked,
    live_provider_capacity,
    provider_is_covered,
)
from app.engine.scheduler import start_game
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match, GameState, MatchKind
from app.models.player import Player
from app.models.user import User
from app.request_logging import set_request_trace_context
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


def _seat_name(handle: str, agent_name: str, existing: set[str]) -> str:
    """Derive a public seat name and keep it unique within the match."""
    base = f"{handle}/{agent_name}"
    if len(base) > 40:
        keep = max(1, 40 - len(handle) - 1)
        base = f"{handle}/{agent_name[:keep]}"
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

    agents = await _load_user_agents(db, user.id)
    # Agents already seated in this match can't join again — hide them so adding
    # more (the admin multi-seat flow) only ever shows agents still available.
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
    joinable_agents = []
    for agent, version in agents:
        if agent.kind != AgentKind.AI:
            continue
        if agent.id in seated_agent_ids:
            continue
        provider = agent.provider
        covered = (
            await provider_is_covered(db, user.id, provider)
            if provider is not None
            else False
        )
        provider_label = provider.value if provider is not None else None
        model_label = (
            f"{provider.value}/{version.model}"
            if provider is not None and version is not None
            else None
        )
        joinable_agents.append({
            "agent": agent,
            "version": version,
            "provider_label": provider_label,
            "model_label": model_label,
            "ready": covered and version is not None,
        })
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "game": match,
            "game_theme": _game_theme(match),
            "player_count": await _player_count(db, match.id),
            "agents": joinable_agents,
            "any_agents": bool(joinable_agents),
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
    bypass_capacity: bool = False,
) -> Player:
    """Validate one of *user*'s agents and build its Player row for *match*.

    Runs the full per-agent gate (ownership, provider coverage, valid model,
    connection capacity, not-already-seated) and derives a unique seat name.
    Mutates *existing_seats* with the new seat so a batch added in one request
    still gets distinct seats. Does not commit — the caller owns the transaction
    so a failure on any agent rolls back the whole batch. Raises HTTPException on
    any problem, naming the agent so the admin knows which one failed.

    *bypass_capacity* skips the SUM-based concurrency cap so an admin can seat an
    agent that is already busy in another active match (the cap is "how many
    matches my machine can serve at once", not a per-agent lock — admins testing
    want to overcommit on purpose). Coverage is still required: the agent must
    still have a live connection, or it genuinely cannot play.
    """
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
    provider = selected_agent.provider
    if provider is None:
        raise HTTPException(
            status_code=409, detail=f"{selected_agent.name} has no provider configured."
        )
    covered = await provider_is_covered(db, user.id, provider)
    if not covered:
        raise HTTPException(
            status_code=409,
            detail=f"No live connection runs {provider.value}. Start a machine first.",
        )
    allowed_models = PROVIDER_MODELS.get(provider.value, [])
    if allowed_models and version.model not in allowed_models:
        raise HTTPException(status_code=400, detail="That model is not valid for this provider.")
    # SUM-based join gate: active count vs. sum of capacities over live
    # connections. Admins bypass it so they can seat an agent that is already
    # busy in another match (e.g. for testing).
    if not bypass_capacity:
        active_match_count = await active_matches_for_provider(db, user.id, provider)
        capacity_sum = await live_provider_capacity(db, user.id, provider)
        if is_join_blocked(active_match_count, capacity_sum):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Your machines are at capacity for {provider.value} "
                    f"({active_match_count}/{capacity_sum} active matches)."
                ),
            )
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
    seat_name = _seat_name(user.handle or user.name or "", selected_agent.name, existing_seats)
    existing_seats.add(seat_name)
    model_label = f"{provider.value}/{version.model}" if version.model else provider.value
    return Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=selected_agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        model_self_report=model_label,
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
        await _seat_user_agent(db, user, match, aid, existing_seats, bypass_capacity=is_admin)
        for aid in selected_ids
    ]
    db.add_all(players)
    await db.commit()

    if match.match_kind == MatchKind.PRACTICE_ARENA.value:
        await start_game(db, match)

    return RedirectResponse(
        url=f"/games/{match.game}/matches/{match.id}", status_code=status.HTTP_303_SEE_OTHER
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
    match_ids = [p.match_id for p in players]
    if not match_ids:
        return templates.TemplateResponse(
            request, "my_matches.html", {"user": user, "is_admin": _is_any_admin(user), "game_sections": []}
        )

    matches = {
        m.id: m
        for m in (await db.execute(select(Match).where(Match.id.in_(match_ids)))).scalars().all()
    }

    count_rows = (await db.execute(
        select(
            Player.match_id,
            func.count(Player.id).label("total"),
            func.sum(case((Agent.kind == AgentKind.BOT, 1), else_=0)).label("bot_count"),
        )
        .join(Agent, Agent.id == Player.agent_id)
        .where(Player.match_id.in_(match_ids))
        .group_by(Player.match_id)
    )).all()
    counts_by_match = {row.match_id: row for row in count_rows}

    sections_map: dict[str, dict] = {}
    for p in players:
        g = matches[p.match_id]
        slug = g.game
        if slug not in sections_map:
            title = {"hoard-hurt-help": "Hoard Hurt Help"}.get(slug, slug.replace("-", " ").title())
            sections_map[slug] = {"title": title, "active": [], "completed": [], "cancelled": []}

        row = counts_by_match.get(p.match_id)
        total = int(row.total or 0) if row else 0
        bot_count = int(row.bot_count or 0) if row else 0
        agent_count = total - bot_count
        parts: list[str] = []
        if agent_count:
            parts.append(f"{agent_count} {'agent' if agent_count == 1 else 'agents'}")
        if bot_count:
            parts.append(f"{bot_count} {'bot' if bot_count == 1 else 'bots'}")
        players_label = ", ".join(parts) if parts else "0 players"

        entry = {
            "id": g.id,
            "name": g.name,
            "state": g.state,
            "agent_id": p.seat_name,
            "watch_url": f"/games/{g.game}/matches/{g.id}",
            "players_label": players_label,
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
