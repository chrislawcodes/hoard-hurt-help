"""Claiming a chosen turn and building the payload served for it.

Split out of ``agent_play_next_turn``. This module answers the question "now
that a turn has been chosen, how do we claim the seat and what do we send the
caller" — the atomic claim (``_claim_pin``) and the serving payload
(``_build_turn_payload``), both reading the lookups ``next_turn_candidates``
gathered rather than re-querying.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.agent_play_reads import (
    RECENT_HISTORY_TURNS,
    _build_current_turn,
    _group_into_turns,
    _load_public_action_records,
    build_public_scoreboard_dicts,
    build_turn_static_dict,
    load_match_players,
    sorted_seat_names,
)
from app.engine.model_provider_match import resolve_seat_model
from app.engine.next_turn import TurnCandidate
from app.engine.next_turn_candidates import CandidateContext
from app.games import get as get_game_module
from app.models.connection import Connection
from app.models.match import Match
from app.models.player import Player


async def _claim_pin(
    db: AsyncSession,
    connection: Connection,
    cand: TurnCandidate,
    ctx: CandidateContext,
    now: datetime,
) -> bool:
    dead_ids = ctx.dead_ids
    player = ctx.player_by_key[(cand.agent_id, cand.match_id)]
    agent_preferred_model = ctx.agent_by_id[cand.agent_id].preferred_model
    claim = cast(
        CursorResult,
        await db.execute(
            update(Player)
            .where(
                Player.id == player.id,
                or_(
                    Player.served_by_connection_id.is_(None),
                    Player.served_by_connection_id == connection.id,
                    Player.served_by_connection_id.in_(dead_ids)
                    if dead_ids
                    else false(),
                ),
            )
            .values(
                served_by_connection_id=connection.id,
                served_pinned_at=now,
                # The AI that actually played this seat is the one the user picked
                # (routing guarantees the serving connection covers it). Stamping
                # it on first claim drives the public "played by …" badge.
                played_provider=player.chosen_provider,
                # And WHICH MODEL, frozen the same moment and by the same
                # function that tells the seat what to run. Before this the
                # export answered by reading the agent's CURRENT preference, so
                # changing an agent's model rewrote what every past match said it
                # played. A match's record has to survive its agent being edited.
                played_model=resolve_seat_model(
                    player.chosen_provider, agent_preferred_model
                ),
            )
        ),
    )
    return claim.rowcount == 1


async def _build_turn_payload(
    db: AsyncSession, cand: TurnCandidate, ctx: CandidateContext
) -> dict[str, object]:
    agent = ctx.agent_by_id[cand.agent_id]
    player = ctx.player_by_key[(cand.agent_id, cand.match_id)]
    version = ctx.version_by_agent_id[cand.agent_id]
    match = (
        await db.execute(select(Match).where(Match.id == cand.match_id))
    ).scalar_one()
    turn = ctx.latest_turn_by_match[cand.match_id]
    all_players = await load_match_players(db, match.id)
    seat_name_by_agent_id = {player.agent_id: player.seat_name for player in all_players}
    # Rolling window, not the whole transcript: this payload is re-served on every
    # poll, so it must stay small (full history is reachable on demand instead).
    history = _group_into_turns(
        await _load_public_action_records(
            db, match.id, all_players, recent_turns=RECENT_HISTORY_TURNS
        )
    )
    scoreboard = build_public_scoreboard_dicts(all_players)
    module = get_game_module(match.game)
    # The static (rules + identity) block, key order and conditional coach_note
    # wire-frozen for the connector (see build_turn_static_dict).
    static = build_turn_static_dict(
        match,
        player,
        all_agent_ids=sorted_seat_names(seat_name_by_agent_id),
        your_strategy=version.strategy_text,
    )
    current = await _build_current_turn(db, turn)
    payload: dict[str, object] = {
        "status": "your_turn",
        "match_id": match.id,
        "game": match.game,
        "agent_id": agent.id,
        "agent_name": agent.name,
        # The AI the user picked for this seat — the connector reads this to run
        # the matching CLI; an MCP client ignores it and just plays as itself.
        "provider": player.chosen_provider,
        # Resolve the seat's model server-side: the agent's optional preferred
        # model when it matches the chosen provider, else that provider's default,
        # else None (connector falls back to its built-in default). The legacy
        # AgentVersion.model is no longer consulted. A provider-mismatched model
        # never reaches the CLI (which would 404, e.g. claude --model gpt-*).
        "model": resolve_seat_model(player.chosen_provider, agent.preferred_model),
        # No top-level `strategy` key: the same text already ships as
        # `static.your_strategy`, which is the one the connector reads. Sending it
        # twice cost ~2KB on every turn and gave a future edit two places to keep
        # in step.
        "version_no": version.version_no,
        "seat_name": seat_name_by_agent_id[player.agent_id],
        "turn_token": turn.turn_token,
        "agent_turn_token": f"{turn.turn_token}:{agent.id}:{match.id}",
        "static": static,
        "history": history,
        "scoreboard": scoreboard,
        "current": current,
    }
    # Per-game state (omitted for games that supply none, e.g. PD — byte-identical).
    private_state = await module.private_state_for(db, match, player)
    if private_state:
        payload["your_private_state"] = private_state
    public_state = await module.public_state_for(db, match, player)
    if public_state:
        payload["public_state"] = public_state
    return payload
