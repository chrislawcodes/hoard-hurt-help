"""Match viewer and live fragment web routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.deps import DbSession, get_current_user, require_user
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match
from app.models.player import Player
from app.models.user import User
from app.read_models.matches import load_match_timeline, load_players
from app.read_models.agent_display import agent_display_name
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _is_game_admin,
    _load_match_or_404,
    _redirect_if_game_slug_mismatch,
    _redirect_to_match,
)
from app.templating import templates

router = APIRouter(tags=["web"])

async def _game_view_context(request: Request, db, match: Match) -> dict:
    """Build the shared context for the game viewer page and its live fragment."""
    user = await get_current_user(request, db)
    g = match
    module = get_game_module(g.game)
    players = await load_players(db, g.id)
    timeline = await load_match_timeline(db, g.id)

    # Public labels for the standings rail + winner credit. The rail shows the
    # agent name and the owner's byline; bots keep the platform credit.
    owner_rows = (
        await db.execute(
            select(Player.seat_name, Agent, User.handle)
            .join(Agent, Agent.id == Player.agent_id)
            .join(User, User.id == Agent.user_id)
            .where(Player.match_id == g.id)
        )
    ).all()
    owner_handles: dict[str, str | None] = {
        seat_name: ("agentludum" if agent.kind == AgentKind.BOT else handle)
        for seat_name, agent, handle in owner_rows
    }
    agent_names: dict[str, str] = {
        seat_name: agent_display_name(agent)
        for seat_name, agent, _handle in owner_rows
    }
    bot_flags: dict[str, bool] = {
        seat_name: agent.kind == AgentKind.BOT for seat_name, agent, _handle in owner_rows
    }

    scoreboard: list[dict[str, Any]] = sorted(
        (
            {
                "agent_id": p.seat_name,
                "display_name": agent_names.get(p.seat_name, p.seat_name),
                "round_score": p.current_round_score,
                "round_wins": p.total_round_wins,
                "owner_handle": owner_handles.get(p.seat_name),
                "is_bot": bot_flags.get(p.seat_name, False),
            }
            for p in players
        ),
        key=lambda r: (-r["round_wins"], -r["round_score"]),
    )
    for i, row in enumerate(scoreboard, start=1):
        row["rank"] = i

    viewer_player = next((p for p in players if user and p.user_id == user.id), None)
    public_state = await module.public_state_for(db, g, viewer_player)
    viewer_seat = viewer_player.seat_name if viewer_player else None

    # The game module owns its replay "story" — the enriched per-turn history and
    # any replay JSON its viewer fragment renders. The platform route stays
    # game-agnostic: it builds the generic skeleton above and merges the module's
    # display payload (history, rc_data, …) into the template context below.
    replay_view = await module.build_replay_view(
        db, g, players, scoreboard, timeline, viewer_seat
    )
    history: list[dict[str, Any]] = replay_view.get("history", [])

    # Keep the server data in chronological order. The template reverses it for
    # the newest-first feed while round navigation can still reason about order.
    rounds: list[dict] = []
    for h in history:
        if not rounds or rounds[-1]["round"] != h["round"]:
            rounds.append({"round": h["round"], "turns": []})
        rounds[-1]["turns"].append(h)
    max_played_round = rounds[-1]["round"] if rounds else 0

    winner_agent_id = None
    if g.winner_player_id:
        winner = (
            await db.execute(select(Player).where(Player.id == g.winner_player_id))
        ).scalar_one_or_none()
        winner_agent_id = winner.seat_name if winner else None

    viewer_prompt_text = None
    viewer_prompt_label = None
    if viewer_player and viewer_player.agent_version_id is not None:
        version = await _load_viewer_prompt_version(db, viewer_player.agent_version_id)
        if version is not None:
            viewer_prompt_text = version.strategy_text
            viewer_prompt_label = f"v{version.version_no} · {version.model}"
    ctx = {
        "user": user,
        "is_admin": _is_any_admin(user),
        "is_game_admin": _is_game_admin(user, g.game),
        "game": g,
        "game_theme": _game_theme(g),
        "scoreboard": scoreboard,
        "history": history,
        "rounds": rounds,
        "max_played_round": max_played_round,
        "winner_agent_id": winner_agent_id,
        "winner_owner_handle": owner_handles.get(winner_agent_id) if winner_agent_id else None,
        "viewer_player_id": viewer_player.id if viewer_player else None,
        "viewer_agent_name": agent_names.get(viewer_player.seat_name) if viewer_player else None,
        "viewer_coach_note": viewer_player.coach_note if viewer_player else None,
        "viewer_coach_note_round": viewer_player.coach_note_round if viewer_player else None,
        "viewer_prompt_text": viewer_prompt_text,
        "viewer_prompt_label": viewer_prompt_label,
        "coaching_enabled": bool(g.coaching) if hasattr(g, "coaching") else True,
        "public_state": public_state,
        # The live-region feed fragment is module-driven, so the template includes
        # whatever fragment this game declares instead of forking on game type.
        "viewer_fragment": module.viewer_fragment(),
        # Whether to render the animated replay stage above the feed. A module
        # turns this on in its replay payload below; default off keeps it game-
        # agnostic (no game-type fork in the template).
        "show_replay_stage": False,
    }
    # Merge the module's display payload (rc_data and any other replay fields).
    # `history` is read above to build the generic round grouping; the rest of the
    # game-specific payload (e.g. rc_data) flows straight into the context.
    for key, value in replay_view.items():
        if key != "history":
            ctx[key] = value
    return ctx


async def _load_viewer_prompt_version(
    db: DbSession,
    agent_version_id: int,
) -> AgentVersion | None:
    """Load the viewer's active prompt version for the coach modal."""
    return (
        await db.execute(select(AgentVersion).where(AgentVersion.id == agent_version_id))
    ).scalar_one_or_none()


@router.get("/games/{game}/matches/{match_id}", response_class=HTMLResponse)
async def game_viewer(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(match, game):
        return redirect
    ctx = await _game_view_context(request, db, match)
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/games/{game}/matches/{match_id}/live", response_class=HTMLResponse)
async def game_live_fragment(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    """Server-rendered live region. SSE events trigger the page to re-fetch this."""
    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(match, game, "/live"):
        return redirect
    ctx = await _game_view_context(request, db, match)
    return templates.TemplateResponse(request, "fragments/live_region.html", ctx)


@router.get("/games/{match_id}/live", include_in_schema=False)
async def legacy_game_live_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/live")


@router.post("/games/{game}/matches/{match_id}/coach-note", response_class=HTMLResponse)
async def post_coach_note(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    note: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Save or clear the operator's sideline coaching note for the next round."""
    from app.read_models.agent_display import agent_display_name

    match = await _load_match_or_404(db, match_id)
    if redirect := _redirect_if_game_slug_mismatch(match, game):
        return redirect  # type: ignore[return-value]
    if match.state.value != "active":
        raise HTTPException(status_code=409, detail="Match is not active.")
    player_row = (
        await db.execute(
            select(Player, Agent)
            .join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id == match_id, Agent.user_id == user.id)
        )
    ).one_or_none()
    if player_row is None:
        raise HTTPException(status_code=403, detail="You are not a player in this match.")
    player, agent = player_row
    viewer_prompt_text = None
    viewer_prompt_label = None
    if player.agent_version_id is not None:
        version = await _load_viewer_prompt_version(db, player.agent_version_id)
        if version is not None:
            viewer_prompt_text = version.strategy_text
            viewer_prompt_label = f"v{version.version_no} · {version.model}"

    note = note.strip()[:280]
    if note:
        player.coach_note = note
        player.coach_note_round = match.current_round + 1
    else:
        player.coach_note = None
        player.coach_note_round = None
    await db.commit()
    await db.refresh(match)
    await db.refresh(player)

    return templates.TemplateResponse(
        request,
        "fragments/coach_panel.html",
        {
            "game": match,
            "viewer_player_id": player.id,
            "viewer_agent_name": agent_display_name(agent),
            "viewer_coach_note": player.coach_note,
            "viewer_coach_note_round": player.coach_note_round,
            "viewer_prompt_text": viewer_prompt_text,
            "viewer_prompt_label": viewer_prompt_label,
            "coaching_enabled": bool(match.coaching) if hasattr(match, "coaching") else True,
        },
    )
