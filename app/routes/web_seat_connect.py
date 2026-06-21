"""Held-seat connect screens: the post-join countdown and its HTMX poll."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.aware_datetime import ensure_aware
from app.deps import DbSession, require_user
from app.engine.connection_health import ProviderReadiness
from app.engine.seat_hold import SEAT_HOLD_SECONDS, confirm_seat_if_live
from app.models.match import Match
from app.models.player import Player
from app.models.user import User
from app.routes.connections_connect_guide import _play_prompt
from app.routes.web_player_shared import (
    _hx_redirect,
    _seat_provider_label,
    _seat_provider_readiness,
)
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _load_owned_player_match_or_404,
)
from app.templating import templates

router = APIRouter(tags=["web"])

# On the held-seat page, a "returning" provider (set up on a connection) is told
# to just paste the play-prompt to wake it. If it's still wired into the user's
# AI client, it checks in within seconds. So if it hasn't come online after this
# grace window, the MCP server has almost certainly dropped out of the client —
# pasting won't help, and we escalate the poll to a "reconnect your server" CTA.
# This is the liveness check: we don't trust the DB "configured" flag, we watch
# whether the connection actually comes online and act when it doesn't.
_STALL_WAKE_SECONDS = 45


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
