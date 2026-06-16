"""Connection-level "what do I do next" fan-out for the agent-play service.

A single connection may drive several agents across several active matches. These
functions gather every turn the connection is allowed to claim, pick/claim one
(or all) under the turn-routing rules, and build the serving payload. This layer
sits above ``agent_play_reads`` and ``agent_play_guards``; the per-match verbs do
not import it and it does not import them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import app.db as db_module
from app.aware_datetime import ensure_aware
from app.engine.agent_idle import IdleStatus, compute_idle_status
from app.engine.connection_activity import mark_polled
from app.engine.agent_play_guards import (
    _LONG_POLL_HOLD_SECONDS,
    _LONG_POLL_INTERVAL_SECONDS,
)
from app.engine.agent_play_reads import (
    _build_current_turn,
    _group_into_turns,
    _load_public_action_records,
)
from app.engine.next_turn import TurnCandidate, select_next_turn
from app.engine.turn_routing import (
    ConnectionRouteState,
    TurnPin,
    can_connection_claim_turn,
    connection_is_dead,
)
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User

logger = logging.getLogger(__name__)


async def _load_route_states(
    db: AsyncSession, connection: Connection
) -> tuple[dict[int, ConnectionRouteState], ConnectionRouteState]:
    conns = (
        (
            await db.execute(
                select(Connection).where(Connection.user_id == connection.user_id)
            )
        )
        .scalars()
        .all()
    )
    conn_ids = [conn.id for conn in conns]
    enabled_by_conn: dict[int, set[str]] = {}
    if conn_ids:
        cp_rows = (
            (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id.in_(conn_ids),
                        ConnectionProviderRow.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in cp_rows:
            enabled_by_conn.setdefault(row.connection_id, set()).add(row.provider.value)

    def _state(conn: Connection) -> ConnectionRouteState:
        return ConnectionRouteState(
            connection_id=conn.id,
            enabled_providers=frozenset(enabled_by_conn.get(conn.id, set())),
            paused=conn.status == ConnectionStatus.PAUSED,
            deleted=conn.deleted_at is not None,
            last_seen_at=conn.last_seen_at,
        )

    by_id = {conn.id: _state(conn) for conn in conns}
    polling = by_id.get(connection.id) or _state(connection)
    return by_id, polling


async def _collect_candidates(
    db: AsyncSession,
    connection: Connection,
    now: datetime,
    *,
    agent_id: int | None = None,
) -> tuple[list[TurnCandidate], dict[str, object]]:
    connections_by_id, polling_state = await _load_route_states(db, connection)

    # When agent_id is given, restrict to that single agent so a caller running one
    # parallel loop per agent only ever sees (and claims) its own agent's turn.
    agents_stmt = (
        select(Agent, Player, Match, AgentVersion)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
        .where(
            Agent.user_id == connection.user_id,
            Agent.kind == AgentKind.AI,
            Agent.status == AgentStatus.ACTIVE,
            Agent.archived_at.is_(None),
            Player.left_at.is_(None),
            Match.state == GameState.ACTIVE,
        )
    )
    if agent_id is not None:
        agents_stmt = agents_stmt.where(Agent.id == agent_id)
    agent_rows = (await db.execute(agents_stmt)).all()

    latest_turn_by_match: dict[str, Turn] = {}
    player_by_key: dict[tuple[int, str], Player] = {}
    agent_by_id: dict[int, Agent] = {}
    version_by_agent_id: dict[int, AgentVersion] = {}
    dead_ids = [
        cid
        for cid, state in connections_by_id.items()
        if connection_is_dead(state, now=now)
    ]

    if not agent_rows:
        return [], {
            "agent_by_id": agent_by_id,
            "player_by_key": player_by_key,
            "version_by_agent_id": version_by_agent_id,
            "latest_turn_by_match": latest_turn_by_match,
            "dead_ids": dead_ids,
        }

    for agent, player, match, version in agent_rows:
        if version is None:
            logger.warning(
                "next-turn: agent %s (connection %s) has no current version; skipping",
                agent.id,
                connection.id,
            )
            continue
        if agent.provider is None:
            logger.warning("next-turn: AI agent %s has no provider; skipping", agent.id)
            continue
        pin = TurnPin(
            served_by_connection_id=player.served_by_connection_id,
            served_pinned_at=player.served_pinned_at,
        )
        if not can_connection_claim_turn(
            polling_state,
            agent.provider,
            pin,
            now=now,
            connections_by_id=connections_by_id,
        ):
            continue
        player_by_key[(agent.id, match.id)] = player
        agent_by_id[agent.id] = agent
        version_by_agent_id[agent.id] = version
        if match.id not in latest_turn_by_match:
            turn = (
                await db.execute(
                    select(Turn)
                    .where(Turn.match_id == match.id, Turn.resolved_at.is_(None))
                    .order_by(Turn.round.desc(), Turn.turn.desc(), Turn.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if turn is not None:
                latest_turn_by_match[match.id] = turn

    candidates: list[TurnCandidate] = []
    for agent_id, match_id in player_by_key:
        player = player_by_key[(agent_id, match_id)]
        turn = latest_turn_by_match.get(match_id)
        if turn is None:
            continue
        existing = (
            await db.execute(
                select(TurnSubmission.id).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == player.id,
                    TurnSubmission.was_defaulted.is_(False),
                )
            )
        ).first()
        if existing is not None:
            continue
        candidates.append(
            TurnCandidate(
                match_id=match_id,
                round=turn.round,
                turn=turn.turn,
                deadline=ensure_aware(turn.deadline_at),
                agent_id=agent_id,
            )
        )
    return candidates, {
        "agent_by_id": agent_by_id,
        "player_by_key": player_by_key,
        "version_by_agent_id": version_by_agent_id,
        "latest_turn_by_match": latest_turn_by_match,
        "dead_ids": dead_ids,
    }


async def _claim_pin(
    db: AsyncSession,
    connection: Connection,
    cand: TurnCandidate,
    ctx: dict[str, object],
    now: datetime,
) -> bool:
    player_by_key = cast(dict[tuple[int, str], Player], ctx["player_by_key"])
    dead_ids = cast(list[int], ctx["dead_ids"])
    player = player_by_key[(cand.agent_id, cand.match_id)]
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
            .values(served_by_connection_id=connection.id, served_pinned_at=now)
        ),
    )
    return claim.rowcount == 1


async def _build_turn_payload(
    db: AsyncSession, cand: TurnCandidate, ctx: dict[str, object]
) -> dict[str, object]:
    agent_by_id = cast(dict[int, Agent], ctx["agent_by_id"])
    player_by_key = cast(dict[tuple[int, str], Player], ctx["player_by_key"])
    version_by_agent_id = cast(dict[int, AgentVersion], ctx["version_by_agent_id"])
    latest_turn_by_match = cast(dict[str, Turn], ctx["latest_turn_by_match"])
    agent = agent_by_id[cand.agent_id]
    player = player_by_key[(cand.agent_id, cand.match_id)]
    version = version_by_agent_id[cand.agent_id]
    match = (
        await db.execute(select(Match).where(Match.id == cand.match_id))
    ).scalar_one()
    turn = latest_turn_by_match[cand.match_id]
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == match.id))).scalars().all()
    )
    seat_name_by_agent_id = {player.agent_id: player.seat_name for player in all_players}
    history = _group_into_turns(await _load_public_action_records(db, match.id, all_players))
    scoreboard = [
        {
            "agent_id": seat_name_by_agent_id[p.agent_id],
            "round_score": p.current_round_score,
            "round_wins": p.total_round_wins,
        }
        for p in sorted(all_players, key=lambda p: (-p.current_round_score, p.seat_name))
    ]
    module = get_game_module(match.game)
    your_agent_id = seat_name_by_agent_id[player.agent_id]
    all_agent_ids = sorted(seat_name_by_agent_id.values())
    static = {
        "match_id": match.id,
        "game_id": match.id,
        "game": match.game,
        "rules_version": match.rules_version,
        "rules": module.rules_text(match.total_rounds, match.turns_per_round),
        "base_prompt": module.agent_base_prompt(
            your_agent_id=your_agent_id,
            all_agent_ids=all_agent_ids,
            total_rounds=match.total_rounds,
            turns_per_round=match.turns_per_round,
        ),
        "total_rounds": match.total_rounds,
        "turns_per_round": match.turns_per_round,
        "your_agent_id": your_agent_id,
        "all_agent_ids": all_agent_ids,
        "your_strategy": version.strategy_text,
    }
    if player.coach_note and player.coach_note_round == match.current_round:
        static["coach_note"] = player.coach_note
    current = await _build_current_turn(db, turn)
    payload: dict[str, object] = {
        "status": "your_turn",
        "match_id": match.id,
        "game": match.game,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "provider": agent.provider.value if agent.provider is not None else None,
        "model": version.model,
        "strategy": version.strategy_text,
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


async def _serve_one_turn(
    db: AsyncSession,
    connection: Connection,
    now: datetime,
    *,
    agent_id: int | None = None,
) -> dict[str, object] | None:
    candidates, ctx = await _collect_candidates(db, connection, now, agent_id=agent_id)
    chosen = select_next_turn(candidates)
    if chosen is None:
        return None
    if not await _claim_pin(db, connection, chosen, ctx, now):
        await db.rollback()
        return None
    await db.commit()
    return await _build_turn_payload(db, chosen, ctx)


def _idle_payload(idle: IdleStatus, *, waiting_poll_hint: int) -> dict[str, object]:
    """Build the response for a poll that has no turn to serve.

    When the caller has a live or upcoming game, this is a plain ``waiting`` reply
    (a turn is coming; keep polling). When the caller has NO game at all, it's a
    ``no_game`` reply that carries ``idle_seconds`` and, once the idle window has
    elapsed, ``should_stop`` so an interactive client can stop polling. The
    always-on connector ignores ``should_stop`` and keeps running by design.
    """
    if idle.has_game:
        return {"status": "waiting", "next_poll_after_seconds": waiting_poll_hint}
    payload: dict[str, object] = {
        "status": "no_game",
        "next_poll_after_seconds": waiting_poll_hint,
        "idle_seconds": idle.idle_seconds,
        "should_stop": idle.should_stop,
    }
    if idle.stop_reason is not None:
        payload["stop_reason"] = idle.stop_reason
    return payload


async def get_next_turn(
    db: AsyncSession,
    connection: Connection,
    *,
    hold_seconds: float = _LONG_POLL_HOLD_SECONDS,
    interval_seconds: float = _LONG_POLL_INTERVAL_SECONDS,
    agent_id: int | None = None,
) -> dict[str, object]:
    held = max(0.0, hold_seconds) > 0.0
    waiting_poll_hint = 2 if held else 30
    deadline = asyncio.get_event_loop().time() + max(0.0, hold_seconds)

    now = datetime.now(timezone.utc)
    # The play-loop heartbeat: reaching here means the AI is actively polling for
    # turns. Stamp it (throttled) before serving so seating can tell a running loop
    # from a one-off sign-in. Its own commit, so the later rollbacks don't undo it.
    await mark_polled(db, connection, now=now)
    served = await _serve_one_turn(db, connection, now, agent_id=agent_id)
    if served is not None:
        return served

    # No turn right now: decide between "waiting" (game coming) and "no_game"
    # (nothing coming) BEFORE the long-poll hold. If the caller has no game at all
    # there's nothing to long-poll for, so reply at once with the idle hint.
    idle = await compute_idle_status(db, connection, now=now)
    if not idle.has_game:
        await db.rollback()
        return _idle_payload(idle, waiting_poll_hint=waiting_poll_hint)

    connection_id = connection.id
    await db.rollback()

    loop = asyncio.get_event_loop()
    while loop.time() < deadline:
        await asyncio.sleep(max(0.0, min(interval_seconds, deadline - loop.time())))
        async with db_module.SessionLocal() as check_db:
            fresh = (
                await check_db.execute(
                    select(Connection)
                    .options(joinedload(Connection.user).load_only(User.disabled_at))
                    .where(Connection.id == connection_id)
                )
            ).scalar_one_or_none()
            if (
                fresh is None
                or fresh.deleted_at is not None
                or fresh.status == ConnectionStatus.PAUSED
                or fresh.user.disabled_at is not None
            ):
                break
            served = await _serve_one_turn(
                check_db, fresh, datetime.now(timezone.utc), agent_id=agent_id
            )
        if served is not None:
            return served

    return {"status": "waiting", "next_poll_after_seconds": waiting_poll_hint}


async def get_next_turns(db: AsyncSession, connection: Connection) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    candidates, ctx = await _collect_candidates(db, connection, now)
    ordered = sorted(
        candidates,
        key=lambda cand: (cand.deadline, cand.match_id, cand.round, cand.turn, cand.agent_id),
    )
    claimed = [cand for cand in ordered if await _claim_pin(db, connection, cand, ctx, now)]
    await db.commit()
    if not claimed:
        idle = await compute_idle_status(db, connection, now=now)
        return _idle_payload(idle, waiting_poll_hint=30)
    turns = [await _build_turn_payload(db, cand, ctx) for cand in claimed]
    return {"status": "your_turn", "turns": turns}
