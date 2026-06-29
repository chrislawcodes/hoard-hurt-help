"""Match viewer and live fragment web routes.

This is the thin route layer: it owns the HTTP endpoints and template rendering
and delegates all page-data assembly to :mod:`app.routes.web_viewer_context`.
The builders are imported here so existing callers that did
``from app.routes.web_viewer import _game_view_context`` keep working.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.deps import DbSession, require_user
from app.models.agent import Agent, AgentKind
from app.models.player import Player
from app.models.user import User
from app.routes.web_support import (
    GameScopedMatch,
    GameScopedMatchToViewer,
    _redirect_to_match,
)
from app.routes.web_viewer_context import (
    _game_view_context,
    _load_viewer_prompt_version,
)
from app.templating import templates

router = APIRouter(tags=["web"])

@router.get("/games/{game}/matches/{match_id}", response_class=HTMLResponse)
async def game_viewer(
    match: GameScopedMatch,
    request: Request,
    db: DbSession,
):
    ctx = await _game_view_context(request, db, match)
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/games/{game}/matches/{match_id}/live", response_class=HTMLResponse)
async def game_live_fragment(
    match: GameScopedMatch,
    request: Request,
    db: DbSession,
):
    """Server-rendered live region. SSE events trigger the page to re-fetch this."""
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
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    match: GameScopedMatchToViewer,
    note: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Save or clear the operator's sideline coaching note for the next round.

    A wrong ``{game}`` slug 301-redirects to the bare viewer URL, raised by the
    ``GameScopedMatchToViewer`` dependency before this body runs (``user`` resolves
    first, so a signed-out request still 401s before the redirect, as before).
    """
    from app.read_models.agent_display import agent_display_name

    if match.state.value != "active":
        raise HTTPException(status_code=409, detail="Match is not active.")
    player_rows = (
        await db.execute(
            select(Player, Agent)
            .join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id == match.id, Agent.user_id == user.id)
        )
    ).all()
    if not player_rows:
        raise HTTPException(status_code=403, detail="You are not a player in this match.")
    # Coaching targets the AI agent you sent — a human seat has no strategy to
    # coach. Since #478 a user can hold both a human and an agent seat here, so a
    # one-row fetch would raise for them; prefer the agent seat, else the only one.
    player, agent = next(
        ((p, a) for (p, a) in player_rows if a.kind != AgentKind.HUMAN),
        player_rows[0],
    )
    viewer_prompt_text = None
    viewer_prompt_label = None
    if player.agent_version_id is not None:
        version = await _load_viewer_prompt_version(db, player.agent_version_id)
        if version is not None:
            viewer_prompt_text = version.strategy_text
            viewer_prompt_label = f"v{version.version_no}"

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
