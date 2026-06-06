"""Guide, runner download, join, and player dashboard web routes."""

import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path as FsPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select

from app.config import PROVIDER_MODELS, settings
from app.deps import DbSession, get_current_user, require_user, require_user_with_handle
from app.engine.scheduler import start_game
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionStatus
from app.models.match import Match, GameState, MatchKind
from app.models.player import Player
from app.models.user import User
from app.request_logging import set_request_trace_context
from app.routes.web_support import (
    _game_theme,
    _is_admin,
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
_BOT_LIVE_WINDOW = timedelta(seconds=90)


def _is_warm(connection: Connection) -> bool:
    """True if this connection's runner contacted the server in the last 90 seconds."""
    ls = connection.last_seen_at
    if ls is None:
        return False
    aware = ls if ls.tzinfo is not None else ls.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - aware <= _BOT_LIVE_WINDOW


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
            "is_admin": _is_admin(user),
            "title": name.replace("-", " ").title(),
            "body": path.read_text(encoding="utf-8"),
        },
    )


# Chained-session agent runner. ONE script now drives every CLI provider — it
# reads the connection's configured provider from the server and calls the matching CLI
# (claude/codex/gemini). The old per-provider filenames are kept as aliases so an
# older setup message still fetches a working runner; they all serve the same file.
# Allowlisted by exact filename below.
_UNIFIED_RUNNER = FsPath("scripts/agentludum_agent.py")
_AGENT_RUNNERS: dict[str, FsPath] = {
    "agentludum_agent.py": _UNIFIED_RUNNER,
    "agentludum_agent_codex.py": _UNIFIED_RUNNER,
    "agentludum_agent_gemini.py": _UNIFIED_RUNNER,
}


@router.get("/runners/{name}", include_in_schema=False)
async def agent_runner_script(name: Annotated[str, Path()]) -> FileResponse:
    """Serve a chained-session agent runner so the setup `curl` fetches it.

    Allowlisted by exact filename — the path never comes from the request, so
    there's no traversal surface. Single source of truth: this streams the
    repo's scripts/<name>, so the downloaded runner always matches this server.
    """
    path = _AGENT_RUNNERS.get(name)
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/x-python", filename=name)


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
) -> list[tuple[Agent, AgentVersion | None, Connection | None]]:
    rows = (
        await db.execute(
            select(Agent, AgentVersion, Connection)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .join(Connection, Connection.id == Agent.connection_id, isouter=True)
            .where(Agent.user_id == user_id, Agent.archived_at.is_(None))
            .order_by(Agent.created_at.desc(), Agent.id.desc())
        )
    ).all()
    return [(agent, version, connection) for agent, version, connection in rows]


async def _active_match_count_for_connection(db: DbSession, connection_id: int) -> int:
    result = await db.scalar(
        select(func.count(func.distinct(Match.id)))
        .select_from(Agent)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .where(
            Agent.connection_id == connection_id,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state == GameState.ACTIVE,
        )
    )
    return int(result or 0)


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
    joinable_agents = [
        {
            "agent": agent,
            "version": version,
            "connection": connection,
            "provider_label": connection.provider.value if connection else None,
            "model_label": f"{connection.provider.value}/{version.model}" if connection and version else None,
            "ready": (
                agent.kind == AgentKind.AI
                and connection is not None
                and connection.status == ConnectionStatus.ACTIVE
                and _is_warm(connection)
                and version is not None
            ),
        }
        for agent, version, connection in agents
        if agent.kind == AgentKind.AI
    ]
    return templates.TemplateResponse(
        request,
        "join.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": match,
            "game_theme": _game_theme(match),
            "player_count": await _player_count(db, match.id),
            "agents": joinable_agents,
            "any_agents": bool(joinable_agents),
            "base_url": settings.base_url,
            "error": None,
        },
    )


@router.post("/games/{game}/matches/{match_id}/join")
async def join_submit(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    agent_id: Annotated[int | None, Form()] = None,
    bot_id: Annotated[int | None, Form()] = None,
    display_name: Annotated[str | None, Form()] = None,
    strategy_prompt: Annotated[str | None, Form()] = None,
):
    """Enter one of the user's agents into a game."""
    selected_agent_id = agent_id if agent_id is not None else bot_id
    if selected_agent_id is None:
        raise HTTPException(status_code=400, detail="Choose an agent.")
    set_request_trace_context(
        request,
        match_id=match_id,
        stage="join_submit",
        agent_id=selected_agent_id,
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

    agent = (
        await db.execute(
            select(Agent, AgentVersion, Connection)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .join(Connection, Connection.id == Agent.connection_id, isouter=True)
            .where(
                Agent.id == selected_agent_id,
                Agent.user_id == user.id,
                Agent.kind == AgentKind.AI,
                Agent.archived_at.is_(None),
            )
        )
    ).one_or_none()
    if agent is None:
        raise HTTPException(404, detail="Agent not found.")
    selected_agent, version, connection = agent
    if version is None:
        raise HTTPException(status_code=409, detail="That agent has no current version.")
    if connection is None or connection.status != ConnectionStatus.ACTIVE or not _is_warm(connection):
        raise HTTPException(status_code=409, detail="That connection is not live yet.")
    allowed_models = PROVIDER_MODELS.get(connection.provider.value, [])
    if allowed_models and version.model not in allowed_models:
        raise HTTPException(status_code=400, detail="That model is not valid for this provider.")
    active_match_count = await _active_match_count_for_connection(db, connection.id)
    if active_match_count >= connection.max_concurrent_games:
        raise HTTPException(
            status_code=409,
            detail="That connection is already running its maximum number of active matches.",
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
        raise HTTPException(status_code=409, detail="That agent is already in this game.")

    existing_seats = set(
        (
            await db.execute(
                select(Player.seat_name).where(Player.match_id == match.id)
            )
        )
        .scalars()
        .all()
    )
    seat_name = _seat_name(user.handle or user.name or "", selected_agent.name, existing_seats)
    model_label = (
        f"{connection.provider.value}/{version.model}" if version.model else connection.provider.value
    )
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=selected_agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        model_self_report=model_label,
    )
    db.add(player)
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
            request, "my_matches.html", {"user": user, "is_admin": _is_admin(user), "game_sections": []}
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
        {"user": user, "is_admin": _is_admin(user), "game_sections": list(sections_map.values())},
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

    agent = (
        await db.execute(
            select(Agent, AgentVersion, Connection)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .join(Connection, Connection.id == Agent.connection_id, isouter=True)
            .where(Agent.id == player.agent_id)
        )
    ).one_or_none()
    current_agent: Agent | None = None
    current_version: AgentVersion | None = None
    current_connection: Connection | None = None
    if agent is not None:
        current_agent, current_version, current_connection = agent

    # The connection key is shown exactly once, right after it is issued — on
    # connect or on an explicit re-issue. We only ever store the argon2 hash, so
    # we cannot show the key again on later visits.
    fresh_key = (
        request.session.pop(f"fresh_connection_key_{current_connection.id}", None)
        if current_connection is not None
        else None
    )

    selected_ai = request.session.pop(f"ai_type_{player.id}", None)
    pre_game = game.state in (GameState.SCHEDULED, GameState.REGISTERING)

    return templates.TemplateResponse(
        request,
        "connection.html",
        {
            "user": user,
            "is_admin": _is_admin(user),
            "game": game,
            "game_theme": _game_theme(game),
            "player": player,
            "agent": current_agent,
            "version": current_version,
            "connection": current_connection,
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


@router.post("/me/players/{player_id}/strategy")
async def update_strategy(
    player_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    strategy_prompt: Annotated[str, Form()],
):
    player, game = await _load_owned_player_match_or_404(db, player_id, user.id)
    if game.state in (GameState.ACTIVE, GameState.COMPLETED):
        raise HTTPException(409, detail="Strategy locked after game starts.")
    clean_strategy = strategy_prompt.strip()
    if not clean_strategy:
        raise HTTPException(status_code=400, detail="Strategy text is required.")
    version = (
        await db.execute(select(AgentVersion).where(AgentVersion.id == player.agent_version_id))
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Agent version not found.")
    version.strategy_text = clean_strategy
    await db.commit()
    return RedirectResponse(
        url=f"/me/players/{player.id}?saved=1", status_code=status.HTTP_303_SEE_OTHER
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
