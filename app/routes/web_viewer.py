"""Match viewer and live fragment web routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.agent_prompt import MESSAGE_MAX_LENGTH
from app.aware_datetime import ensure_aware
from app.deps import DbSession, get_current_user, require_user
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.models.user import User
from app.read_models.matches import load_match_timeline, load_players
from app.read_models.agent_display import agent_display_name
from app.routes.provider_labels import PROVIDER_LABELS
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
    kind_by_seat: dict[str, AgentKind] = {
        seat_name: agent.kind for seat_name, agent, _handle in owner_rows
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
                # Provider that actually played this seat (Claude/Gemini/…), shown
                # as a badge. None for bots and seats not yet served.
                "provider": (
                    None
                    if bot_flags.get(p.seat_name, False) or not p.played_provider
                    else PROVIDER_LABELS.get(p.played_provider, p.played_provider.title())
                ),
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
    play_ctx = await _build_human_play_context(
        db, g, players, viewer_player, kind_by_seat
    )
    # Leave-CTA flag: a seated human can leave (pre-start frees the seat, in-match
    # flips it to autopilot). The join entrance is the "Enter game" link, which
    # leads to the join screen where "Play as yourself" is the first choice.
    viewer_seat_human = (
        viewer_player is not None
        and kind_by_seat.get(viewer_player.seat_name) == AgentKind.HUMAN
    )
    play_ctx["viewer_seat_human"] = viewer_seat_human

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
            viewer_prompt_label = f"v{version.version_no}"
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
    ctx.update(play_ctx)
    # Merge the module's display payload (rc_data and any other replay fields).
    # `history` is read above to build the generic round grouping; the rest of the
    # game-specific payload (e.g. rc_data) flows straight into the context.
    for key, value in replay_view.items():
        if key != "history":
            ctx[key] = value
    return ctx


async def _build_turn_talk(
    db: DbSession,
    turn: Turn,
    players: list[Player],
    viewer_player: Player,
) -> list[dict[str, Any]]:
    """This turn's talk for the act-phase panel: who said what, who stayed quiet.

    Speakers come first in the order they spoke; silent opponents follow as
    "stayed quiet". The viewer's own message is left out — they wrote it.
    """
    rows = (
        await db.execute(
            select(TurnMessage.player_id, TurnMessage.text)
            .where(TurnMessage.turn_id == turn.id)
            .order_by(TurnMessage.id)
        )
    ).all()
    text_by_player = {pid: (text or "").strip() for pid, text in rows}
    seat_by_player = {p.id: p.seat_name for p in players}

    talk: list[dict[str, Any]] = []
    spoke: set[int] = set()
    for pid, _text in rows:
        said = text_by_player.get(pid, "")
        if pid == viewer_player.id or not said or pid in spoke:
            continue
        spoke.add(pid)
        talk.append({"who": seat_by_player.get(pid, "player"), "text": said, "quiet": False})
    for p in players:
        if p.left_at is not None or p.id == viewer_player.id or p.id in spoke:
            continue
        talk.append({"who": p.seat_name, "text": "", "quiet": True})
    return talk


async def _build_human_play_context(
    db: DbSession,
    match: Match,
    players: list[Player],
    viewer_player: Player | None,
    kind_by_seat: dict[str, AgentKind],
) -> dict[str, Any]:
    """Per-viewer play-panel state + the everyone-visible 'waiting on N' count.

    Returns a flat dict merged into the viewer context. When there is no open
    turn (or the match isn't active) the panel-specific keys stay falsy so the
    template renders nothing extra.
    """
    base: dict[str, Any] = {
        "viewer_is_human": False,
        "viewer_on_autopilot": False,
        "can_play": False,
        "play_phase": None,
        "play_deadline_at": None,
        "play_submitted": False,
        "play_action": None,
        "play_target": None,
        "play_targets": [],
        "play_talk": [],
        "waiting_on": None,
        "message_max": MESSAGE_MAX_LENGTH,
    }
    if match.state != GameState.ACTIVE:
        return base

    turn = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.match_id == match.id, Turn.resolved_at.is_(None))
                .order_by(Turn.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if turn is None:
        return base

    phase = turn.phase
    active = [p for p in players if p.left_at is None]
    if phase == "talk":
        acted_ids = set(
            (
                await db.execute(
                    select(TurnMessage.player_id).where(
                        TurnMessage.turn_id == turn.id,
                        TurnMessage.was_defaulted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
    else:
        acted_ids = set(
            (
                await db.execute(
                    select(TurnSubmission.player_id).where(
                        TurnSubmission.turn_id == turn.id,
                        TurnSubmission.was_defaulted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )

    base["play_phase"] = phase
    base["play_deadline_at"] = ensure_aware(turn.deadline_at).isoformat()
    base["waiting_on"] = sum(1 for p in active if p.id not in acted_ids)

    if (
        viewer_player is not None
        and kind_by_seat.get(viewer_player.seat_name) == AgentKind.HUMAN
    ):
        base["viewer_is_human"] = True
        base["viewer_on_autopilot"] = viewer_player.autopilot_at is not None
        submitted = viewer_player.id in acted_ids
        base["play_submitted"] = submitted
        base["can_play"] = viewer_player.autopilot_at is None
        base["play_targets"] = [
            p.seat_name for p in active if p.id != viewer_player.id
        ]
        if phase == "act":
            # Reveal this turn's talk so the human reads what was said before
            # acting — the same transcript the bots get in their act-phase
            # prompt. This open turn isn't in the feed yet (the feed only shows
            # resolved turns), so without this the human would act blind.
            base["play_talk"] = await _build_turn_talk(db, turn, players, viewer_player)
        if phase == "act" and submitted:
            sub = (
                await db.execute(
                    select(TurnSubmission).where(
                        TurnSubmission.turn_id == turn.id,
                        TurnSubmission.player_id == viewer_player.id,
                    )
                )
            ).scalar_one_or_none()
            if sub is not None:
                base["play_action"] = sub.action
                if sub.target_player_id is not None:
                    target = next(
                        (p for p in players if p.id == sub.target_player_id), None
                    )
                    base["play_target"] = target.seat_name if target else None
    return base


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
