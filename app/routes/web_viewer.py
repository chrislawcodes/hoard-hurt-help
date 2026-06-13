"""Match viewer and live fragment web routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.deps import DbSession, get_current_user, require_user
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import Match
from app.models.player import Player
from app.models.user import User
from app.read_models.matches import load_match_timeline, load_players
from app.read_models.agent_display import agent_display_name
from app.routes.viewer_presentation import (
    _build_rc_data,
    _feed_sort_key,
    _move_effect_for,
    _turn_groups,
    _turn_headline,
    _turn_summary,
)
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

    history: list[dict[str, Any]] = []
    viewer_player = next((p for p in players if user and p.user_id == user.id), None)

    # Per-turn pact/betrayal signals for the replay. A "pact" is a mutual HELP in
    # the same turn; a "betrayal" is a HURT aimed at last turn's pact partner.
    prev_mutual: set[frozenset[str]] = set()
    # Carried across turns to narrate a deterministic play-by-play headline.
    prev_actions: list[dict[str, Any]] = []
    prev_leader: str | None = None
    inround: dict[str, int] = {}
    inround_round: int | None = None
    for seq, t in enumerate(timeline, start=1):
        messages: list[dict[str, Any]] = [
            {
                "agent_id": message.agent_id,
                "text": message.text,
                "thinking": message.thinking,
                "was_defaulted": message.was_defaulted,
            }
            for message in t.messages
        ]
        actions: list[dict[str, Any]] = []
        for action in t.actions:
            actor_delta, target_delta = _move_effect_for(g.game, action.action)
            actions.append(
                {
                    "agent_id": action.agent_id,
                    "action": action.action,
                    "target_id": action.target_id,
                    # Nominal per-move effect, attributed to who it lands on.
                    "actor_delta": actor_delta,
                    "target_delta": target_delta,
                    "thinking": action.thinking,
                    "was_defaulted": action.was_defaulted,
                    "mutual": False,
                    "betrayal": False,
                }
            )

        # Tag this turn's pacts (mutual HELP) and betrayals (HURT on last turn's
        # pact partner), so the feed can mark them without re-deriving in JS.
        helps = {
            a["agent_id"]: a["target_id"]
            for a in actions
            if a["action"] == "HELP" and a["target_id"]
        }
        this_mutual: set[frozenset[str]] = set()
        for a in actions:
            tgt = a["target_id"]
            if not tgt:
                continue
            pair = frozenset((a["agent_id"], tgt))
            if a["action"] == "HELP" and helps.get(tgt) == a["agent_id"]:
                a["mutual"] = True
                this_mutual.add(pair)
            elif a["action"] == "HURT" and pair in prev_mutual:
                a["betrayal"] = True
        prev_mutual = this_mutual

        messages_by_agent = {m["agent_id"]: m for m in messages}
        for a in actions:
            paired_message = messages_by_agent.get(a["agent_id"])
            if paired_message is not None:
                a["message"] = paired_message["text"]
                a["message_thinking"] = paired_message["thinking"]
                a["message_was_defaulted"] = paired_message["was_defaulted"]
            else:
                a["message"] = ""
                a["message_thinking"] = ""
                a["message_was_defaulted"] = True

            if a["action"] == "HOARD":
                a["display_action"] = "Hoard"
                a["display_delta"] = a["actor_delta"]
            elif a["action"] == "HELP":
                a["display_action"] = "Help"
                a["display_delta"] = 8 if a["mutual"] else a["target_delta"]
            else:
                a["display_action"] = "HURT"
                a["display_delta"] = a["target_delta"]

        # Running in-round score (resets each round) → who leads, for the
        # play-by-play "lead change" beat.
        if t.round != inround_round:
            inround_round = t.round
            inround = {p.seat_name: 0 for p in players}
        for a in actions:
            if a["action"] == "HOARD":
                inround[a["agent_id"]] = inround.get(a["agent_id"], 0) + 2
            elif a["action"] == "HELP" and a["mutual"]:
                inround[a["agent_id"]] = inround.get(a["agent_id"], 0) + 8
            elif a["action"] == "HELP" and a["target_id"]:
                inround[a["target_id"]] = inround.get(a["target_id"], 0) + 4
            elif a["action"] == "HURT" and a["target_id"]:
                inround[a["target_id"]] = max(0, inround.get(a["target_id"], 0) - 4)
        # Highest score, ties broken alphabetically — deterministic.
        leader = min(inround, key=lambda k: (-inround[k], k)) if inround else None
        headline = _turn_headline(actions, prev_actions, leader, prev_leader, seq)
        prev_leader = leader
        prev_actions = actions

        history.append(
            {
                "seq": seq,
                "round": t.round,
                "turn": t.turn,
                "messages": messages,
                "actions": actions,
                # `actions` stays in submission order for the animation; the feed
                # renders `feed_actions` (highlights first) and `summary` (counts).
                "feed_actions": sorted(actions, key=_feed_sort_key),
                "summary": _turn_summary(actions),
                "groups": _turn_groups(actions),
                "headline": headline,
            }
        )

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

    viewer_seat = viewer_player.seat_name if viewer_player else None
    viewer_prompt_text = None
    viewer_prompt_label = None
    if viewer_player and viewer_player.agent_version_id is not None:
        version = await _load_viewer_prompt_version(db, viewer_player.agent_version_id)
        if version is not None:
            viewer_prompt_text = version.strategy_text
            viewer_prompt_label = f"v{version.version_no} · {version.model}"
    return {
        "user": user,
        "is_admin": _is_any_admin(user),
        "is_game_admin": _is_game_admin(user, g.game),
        "game": g,
        "game_theme": _game_theme(g),
        "scoreboard": scoreboard,
        "history": history,
        # The replay's turn data. Built here (not just in the full-page route) so
        # the live fragment carries fresh turns too — that's what lets an
        # already-open page extend the animation as new turns resolve, instead of
        # staying frozen at the turn count present when the page first loaded.
        "rc_data": _build_rc_data(scoreboard, history, g.turns_per_round, viewer_seat),
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
    }


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
