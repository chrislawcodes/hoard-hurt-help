"""Page-data assembly for the match viewer and its live fragment.

This module holds the context-builder functions that the thin ``web_viewer``
route layer calls. They turn a :class:`Match` (plus the request's viewer) into
the template context dicts the viewer page, the ``/live`` fragment, and the
showcase replay render. No route handlers or template rendering live here — the
builders only assemble data; the routes own ``TemplateResponse``.
"""

from typing import Any

from fastapi import Request
from sqlalchemy import select

from app.agent_prompt import MESSAGE_MAX_LENGTH
from app.aware_datetime import ensure_aware
from app.deps import DbSession, get_current_user
from app.engine.user_match_start import viewer_start_eligibility
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.models.user import User
from app.provider_labels import provider_label
from app.read_models.agent_display import agent_display_name
from app.read_models.matches import load_match_timeline, load_players
from app.routes.web_support import (
    _game_theme,
    _is_any_admin,
    _is_game_admin,
)


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
    # In a single match the seat name is the bot's identity: the turn feed,
    # say-bubbles, and robot-circle captions all narrate by seat name (e.g.
    # "Napoleon strikes Wellington"). Bots' agent_display_name is the shared
    # play-style profile ("Coalition Seeker") — fine for the cross-match
    # leaderboard, but it desyncs the standings rail and robot labels from the
    # play-by-play, and two bots sharing a profile would collide. So show the
    # seat name for bots here; humans/AI agents keep their full agent name.
    agent_names: dict[str, str] = {
        seat_name: (seat_name if agent.kind == AgentKind.BOT else agent_display_name(agent))
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
                    else provider_label(p.played_provider)
                ),
            }
            for p in players
        ),
        key=lambda r: (-r["round_wins"], -r["round_score"]),
    )
    for i, row in enumerate(scoreboard, start=1):
        row["rank"] = i

    # A user can hold two seats in one match since #478 (join as a human AND send
    # an AI agent). Resolve each separately: the human seat you steer drives the
    # play cockpit and the "you" highlight, while the agent you sent drives the
    # strategy/coach panels. The old single `next(...)` returned whichever seat
    # sorted first by name, so a human who also sent an agent silently got the
    # agent seat — and never saw their move controls.
    my_players = [p for p in players if user and p.user_id == user.id]
    viewer_human = next(
        (
            p
            for p in my_players
            if p.left_at is None
            and kind_by_seat.get(p.seat_name) == AgentKind.HUMAN
        ),
        None,
    )
    viewer_agent = next(
        (p for p in my_players if kind_by_seat.get(p.seat_name) != AgentKind.HUMAN),
        None,
    )
    # "You" on the page (cockpit, standings highlight, board perspective): the
    # human seat you steer, else the agent you sent.
    viewer_player = viewer_human or viewer_agent
    # The strategy prompt + coach note describe the agent you sent (a human seat
    # has neither); fall back to the human seat so a solo human still resolves.
    coach_player = viewer_agent or viewer_human
    public_state = await module.public_state_for(db, g, viewer_player)
    viewer_seat = viewer_player.seat_name if viewer_player else None

    # The game module owns its replay "story" — the enriched per-turn history and
    # any replay JSON its viewer fragment renders. Built before the play cockpit
    # so the talk-phase panel can recap the turn that just resolved (the mirror of
    # the act-phase "what was just said" reveal). The platform route stays
    # game-agnostic: it builds the generic skeleton above and merges the module's
    # display payload (history, rc_data, …) into the template context below.
    replay_view = await module.build_replay_view(
        db, g, players, scoreboard, timeline, viewer_seat
    )
    history: list[dict[str, Any]] = replay_view.get("history", [])

    # The cockpit is for the human seat only — pass it, not the agent seat.
    play_ctx = await _build_human_play_context(
        db, g, players, viewer_human, kind_by_seat, history
    )
    # Leave-CTA flag: a seated human can leave (pre-start frees the seat, in-match
    # flips it to autopilot). The join entrance is the "Enter game" link, which
    # leads to the join screen where "Play as yourself" is the first choice.
    viewer_seat_human = viewer_human is not None
    play_ctx["viewer_seat_human"] = viewer_seat_human

    # Solo-start CTA: when this viewer is the only person with a seat in a
    # pre-start match, they can start it now (bots fill the table to the floor).
    start_eligibility = await viewer_start_eligibility(db, g, user)

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
    if coach_player is not None and coach_player.agent_version_id is not None:
        version = await _load_viewer_prompt_version(db, coach_player.agent_version_id)
        if version is not None:
            viewer_prompt_text = version.strategy_text
            viewer_prompt_label = f"v{version.version_no}"
    # Pre-start countdown target for the viewer stage: a big clock in the centre
    # of the robot ring while the match waits to begin. Only a real waiting match
    # gets one — a practice arena starts on join (no fixed time), so it has none.
    countdown_start_iso = (
        ensure_aware(g.scheduled_start).isoformat()
        if g.state in (GameState.SCHEDULED, GameState.REGISTERING)
        and g.match_kind != MatchKind.PRACTICE_ARENA.value
        else None
    )
    ctx = {
        "user": user,
        "is_admin": _is_any_admin(user),
        "is_game_admin": _is_game_admin(user, g.game),
        "game": g,
        "countdown_start_iso": countdown_start_iso,
        "game_theme": _game_theme(g),
        "scoreboard": scoreboard,
        "history": history,
        "rounds": rounds,
        "max_played_round": max_played_round,
        "winner_agent_id": winner_agent_id,
        "winner_owner_handle": owner_handles.get(winner_agent_id) if winner_agent_id else None,
        # The coach panel (strategy link + note) is about the agent you sent.
        "viewer_player_id": coach_player.id if coach_player else None,
        "viewer_agent_name": agent_names.get(viewer_player.seat_name) if viewer_player else None,
        "viewer_coach_note": coach_player.coach_note if coach_player else None,
        "viewer_coach_note_round": coach_player.coach_note_round if coach_player else None,
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
    ctx["viewer_can_start"] = start_eligibility.can_start
    ctx["viewer_start_bots"] = start_eligibility.bots_to_add
    # Merge the module's display payload (rc_data and any other replay fields).
    # `history` is read above to build the generic round grouping; the rest of the
    # game-specific payload (e.g. rc_data) flows straight into the context.
    for key, value in replay_view.items():
        if key != "history":
            ctx[key] = value

    # Player-mode cockpit (spec 018). When the seated viewer is a HUMAN in a LIVE
    # match the page becomes a play cockpit: the input docks at the bottom, feed
    # names carry score chips, and the animated replay steps aside (it returns as
    # the lead once the game is completed). These keys also feed the /live
    # fragment, so chips + standings survive every SSE swap.
    ctx["viewer_seat"] = viewer_seat
    ctx["leader_seat"] = scoreboard[0]["agent_id"] if scoreboard else None
    score_by_name = {row["agent_id"]: row["round_score"] for row in scoreboard}
    ctx["score_by_name"] = score_by_name
    # Order this turn's revealed talk by round score (highest first; silent last),
    # so the live reveal reads in standings order (spec 019).
    talk = play_ctx.get("play_talk") or []
    if talk:
        spoke = [m for m in talk if not m.get("quiet")]
        quiet = [m for m in talk if m.get("quiet")]
        spoke.sort(key=lambda m: (-score_by_name.get(m["who"], 0), m["who"]))
        ctx["play_talk"] = spoke + quiet
    player_mode = bool(play_ctx.get("viewer_is_human")) and g.state == GameState.ACTIVE
    ctx["player_mode"] = player_mode
    if player_mode:
        ctx["show_replay_stage"] = False
    return ctx


async def _build_turn_talk(
    db: DbSession,
    turn: Turn,
    players: list[Player],
    viewer_player: Player,
) -> list[dict[str, Any]]:
    """This turn's talk for the act-phase panel: who said what, who stayed quiet.

    The viewer's own line comes first (flagged ``is_you``) so they can re-read
    what they said before acting — they shouldn't be the one player missing from
    the turn's talk. Other speakers follow in the order they spoke, and silent
    opponents come last (the template folds them into one "stayed quiet" line).
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

    def entry(pid: int, *, is_you: bool) -> dict[str, Any]:
        said = text_by_player.get(pid, "")
        return {
            "who": seat_by_player.get(pid, "player"),
            "text": said,
            "quiet": not said,
            "is_you": is_you,
        }

    # The viewer first, then other speakers in order, then the silent — each
    # player once (``seen`` guards against listing anyone twice).
    talk: list[dict[str, Any]] = [entry(viewer_player.id, is_you=True)]
    seen: set[int] = {viewer_player.id}
    for pid, _text in rows:
        if pid in seen or not text_by_player.get(pid, ""):
            continue
        seen.add(pid)
        talk.append(entry(pid, is_you=False))
    for p in players:
        if p.left_at is not None or p.id in seen:
            continue
        seen.add(p.id)
        talk.append(entry(p.id, is_you=False))
    return talk


async def _build_human_play_context(
    db: DbSession,
    match: Match,
    players: list[Player],
    viewer_player: Player | None,
    kind_by_seat: dict[str, AgentKind],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Per-viewer play-panel state + the everyone-visible 'waiting on N' count.

    Returns a flat dict merged into the viewer context. A seated human in an
    active match is flagged ``viewer_is_human`` even between turns, so the cockpit
    stays in place; the turn-specific keys (phase, deadline, can_play, …) only
    fill in while a turn is open. When the match isn't active everything stays
    falsy and the template renders nothing extra.

    ``history`` is the module's resolved-turn replay (chronological). In the talk
    phase its last entry is the turn that just ended, surfaced as
    ``play_last_result`` so the human reads the outcome before speaking again —
    the mirror of the act phase's ``play_talk`` reveal.
    """
    base: dict[str, Any] = {
        "viewer_is_human": False,
        "viewer_on_autopilot": False,
        "can_play": False,
        "play_phase": None,
        "play_deadline_at": None,
        "play_submitted": False,
        "play_message": "",
        "play_action": None,
        "play_target": None,
        "play_targets": [],
        "play_talk": [],
        "play_last_result": [],
        "play_last_label": None,
        "waiting_on": None,
        "message_max": MESSAGE_MAX_LENGTH,
    }
    if match.state != GameState.ACTIVE:
        return base

    # Identity first: is the viewer a seated human in this match? This does not
    # depend on whether a turn is open right now, so the cockpit stays put in the
    # gap between turns instead of blinking to the spectator view (and back).
    viewer_is_human = (
        viewer_player is not None
        and viewer_player.left_at is None
        and kind_by_seat.get(viewer_player.seat_name) == AgentKind.HUMAN
    )
    if viewer_player is not None and viewer_is_human:
        base["viewer_is_human"] = True
        base["viewer_on_autopilot"] = viewer_player.autopilot_at is not None

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
        # Between turns: no open turn. A seated human keeps the cockpit (set
        # above) but gets no active move form — can_play stays False and
        # play_phase stays None, which the panel renders as a brief wait.
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

    if viewer_player is not None and viewer_is_human:
        # Identity (viewer_is_human / on_autopilot) is set above; here we add the
        # turn-specific move state now that we know a turn is open.
        submitted = viewer_player.id in acted_ids
        base["play_submitted"] = submitted
        base["can_play"] = viewer_player.autopilot_at is None
        base["play_targets"] = [
            p.seat_name for p in active if p.id != viewer_player.id
        ]
        if phase == "talk" and history:
            # Recap the turn that just ended so the human reads the outcome
            # (who did what, for how many points) before speaking again. This is
            # the talk-phase mirror of the act-phase talk reveal below. The open
            # talk turn isn't in `history` (it lists resolved turns only), so the
            # last entry is the turn that just resolved.
            last = history[-1]
            base["play_last_result"] = last["feed_actions"]
            base["play_last_label"] = f"Round {last['round']} · Turn {last['turn']}"
        if phase == "talk" and submitted:
            # Keep the human's already-sent message in the box so the panel
            # reflects what they said (the act-phase mirror of re-checking the
            # chosen action below). Without this the input re-renders empty after
            # submit, which reads as "nothing was sent" even though it was.
            base["play_message"] = (
                await db.execute(
                    select(TurnMessage.text).where(
                        TurnMessage.turn_id == turn.id,
                        TurnMessage.player_id == viewer_player.id,
                    )
                )
            ).scalar_one_or_none() or ""
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
