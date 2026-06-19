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
from typing import Any, cast

from sqlalchemy import false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import app.db as db_module
from app.aware_datetime import ensure_aware
from app.engine.agent_idle import (
    LONG_POLL_INTERVAL_SECONDS,
    IdleStatus,
    compute_idle_status,
    pace_idle,
)
from app.engine.connection_activity import mark_polled
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
from app.models.turn import Turn, TurnMessage, TurnSubmission
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
        pin = TurnPin(
            served_by_connection_id=player.served_by_connection_id,
            served_pinned_at=player.served_pinned_at,
        )
        # Provider-agnostic routing: any of the user's live connections may serve
        # any of the user's agents. The sticky pin (handled inside) still keeps a
        # single connection serving a given seat once it starts.
        if not can_connection_claim_turn(
            polling_state,
            None,
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
        # Talk-phase symmetry with the act check above: a player who has already
        # broadcast their talk message has nothing left to do until the act phase
        # opens. Without this, every poll during the talk->act gap re-serves the
        # same full turn payload (entire history included), which bloats the AI's
        # context and trips client-side loop detectors. Skip it so the loop
        # long-polls and serves the act phase once, when it actually opens.
        if turn.phase == "talk":
            existing_message = (
                await db.execute(
                    select(TurnMessage.id).where(
                        TurnMessage.turn_id == turn.id,
                        TurnMessage.player_id == player.id,
                        TurnMessage.was_defaulted.is_(False),
                    )
                )
            ).first()
            if existing_message is not None:
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


async def agent_identity_for(
    db: AsyncSession,
    connection: Connection,
    *,
    agent_id: int | None = None,
    match_id: str | None = None,
) -> tuple[Match | None, str | None, list[Any], str | None]:
    """Resolve one active agent's match, identity, targets, and strategy.

    This is for the MCP instructions flow, not turn claiming. It looks at the
    user's active AI agents and their live or upcoming matches, but it never
    claims a turn and it does not depend on an open turn window.
    """

    active_agent_ids = sorted(
        (
            await db.execute(
                select(Agent.id).where(
                    Agent.user_id == connection.user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not active_agent_ids:
        return None, None, [], None
    if agent_id is None and len(active_agent_ids) > 1:
        return None, None, active_agent_ids, None

    selected_agent_id = agent_id or active_agent_ids[0]
    if selected_agent_id not in active_agent_ids:
        return None, None, [], None

    candidate_rows = (
        await db.execute(
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
                Match.state.in_(
                    [GameState.ACTIVE, GameState.SCHEDULED, GameState.REGISTERING]
                ),
            )
        )
    ).all()
    if not candidate_rows:
        return None, None, [], None

    rows_by_match_id: dict[str, list[tuple[Agent, Player, Match, AgentVersion | None]]] = {}
    for row in candidate_rows:
        agent, player, match, version = row
        rows_by_match_id.setdefault(match.id, []).append((agent, player, match, version))

    match_rows = [
        rows
        for rows in rows_by_match_id.values()
        if any(agent.id == selected_agent_id for agent, _player, _match, _version in rows)
    ]
    if not match_rows:
        return None, None, [], None
    if match_id is not None:
        match_rows = [rows for rows in match_rows if rows[0][2].id == match_id]
        if not match_rows:
            return None, None, [], None

    ranked_rows: list[tuple[tuple[object, ...], list[tuple[Agent, Player, Match, AgentVersion | None]]]] = []
    for rows in match_rows:
        _agent, _player, match, _version = rows[0]
        current_turn = None
        if match.state == GameState.ACTIVE:
            current_turn = (
                await db.execute(
                    select(Turn)
                    .where(
                        Turn.match_id == match.id,
                        Turn.resolved_at.is_(None),
                    )
                    .order_by(Turn.round.desc(), Turn.turn.desc(), Turn.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if current_turn is not None:
            when = ensure_aware(current_turn.deadline_at)
        elif match.scheduled_start is not None:
            when = ensure_aware(match.scheduled_start)
        else:
            when = datetime.max.replace(tzinfo=timezone.utc)
        ranked_rows.append(((0 if match.state == GameState.ACTIVE else 1, when, match.id), rows))

    selected_rows = min(ranked_rows, key=lambda item: item[0])[1]
    match = selected_rows[0][2]
    your_player = next(
        (player for agent, player, _match, _version in selected_rows if agent.id == selected_agent_id),
        None,
    )
    version = next(
        (version for agent, _player, _match, version in selected_rows if agent.id == selected_agent_id and version is not None),
        None,
    )
    if your_player is None or version is None:
        return None, None, [], None
    seat_name_by_agent_id = {player.agent_id: player.seat_name for _agent, player, _match, _version in selected_rows}
    all_agent_ids = sorted(seat_name_by_agent_id.values())
    return match, seat_name_by_agent_id[your_player.agent_id], all_agent_ids, version.strategy_text


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
            .values(
                served_by_connection_id=connection.id,
                served_pinned_at=now,
                # Record who actually played this seat — the public provider badge
                # reads this, so it survives the connection later being deleted.
                played_provider=(
                    connection.provider.value if connection.provider is not None else None
                ),
            )
        ),
    )
    return claim.rowcount == 1


async def _build_turn_payload(
    db: AsyncSession, cand: TurnCandidate, ctx: dict[str, object], connection: Connection
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
        # The provider is the connection actually serving this turn — agents are
        # no longer tied to one.
        "provider": connection.provider.value if connection.provider is not None else None,
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
    return await _build_turn_payload(db, chosen, ctx, connection)


def _idle_payload(idle: IdleStatus, *, waiting_poll_hint: int) -> dict[str, object]:
    """Build the response for a poll that has no turn to serve.

    When the caller has a live or upcoming game, this is a plain ``waiting`` reply
    (a turn is coming; keep polling). When the caller has NO game at all, it's a
    ``no_game`` reply that carries ``idle_seconds`` and, once the idle window has
    elapsed, ``should_stop`` so an interactive client can stop polling. The
    always-on connector ignores ``should_stop`` and keeps running by design.
    """
    if idle.has_game:
        waiting: dict[str, object] = {
            "status": "waiting",
            "next_poll_after_seconds": waiting_poll_hint,
        }
        if idle.seconds_to_next_start is not None:
            waiting["next_game_starts_in_seconds"] = idle.seconds_to_next_start
        return waiting
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
    agent_id: int | None = None,
    max_hold_seconds: float | None = None,
) -> dict[str, object]:
    """Serve the caller's most urgent turn, or — if none is open — tell it how soon
    to ask again, paced off its soonest game (see :func:`pace_idle`).

    The hold length and the wait number are decided by the server, not the caller.
    ``max_hold_seconds`` caps the long-poll hold (tests pass 0 to return at once
    instead of waiting out a real hold).
    """
    now = datetime.now(timezone.utc)
    # The play-loop heartbeat: reaching here means the AI is actively polling for
    # turns. Stamp it (throttled) before serving so seating can tell a running loop
    # from a one-off sign-in. Its own commit, so the later rollbacks don't undo it.
    await mark_polled(db, connection, now=now)
    served = await _serve_one_turn(db, connection, now, agent_id=agent_id)
    if served is not None:
        return served

    # No turn right now. Pace off the soonest game: a live (or imminent) game
    # long-polls; everything else gets a plain "wait N seconds" and returns at once.
    # Scope to agent_id when a per-agent loop asks, so it paces off its own game.
    idle = await compute_idle_status(db, connection, now=now, agent_id=agent_id)
    hold_seconds, next_poll = pace_idle(idle)
    if max_hold_seconds is not None:
        hold_seconds = min(hold_seconds, max_hold_seconds)

    if hold_seconds <= 0.0:
        await db.rollback()
        return _idle_payload(idle, waiting_poll_hint=next_poll)

    connection_id = connection.id
    await db.rollback()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + hold_seconds
    # One session for the whole hold — no repeated open/close per tick.
    # populate_existing forces each re-query to reflect the live DB row even
    # though the identity map has the Connection from earlier in this session.
    async with db_module.SessionLocal() as check_db:
        while loop.time() < deadline:
            await asyncio.sleep(
                max(0.0, min(LONG_POLL_INTERVAL_SECONDS, deadline - loop.time()))
            )
            fresh = (
                await check_db.execute(
                    select(Connection)
                    .options(joinedload(Connection.user).load_only(User.disabled_at))
                    .where(Connection.id == connection_id)
                    .execution_options(populate_existing=True)
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

    return {"status": "waiting", "next_poll_after_seconds": next_poll}


async def get_next_turns(db: AsyncSession, connection: Connection) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    # Play-loop heartbeat: calling get_next_turns is the AI actively polling for
    # work, exactly like get_next_turn. Stamp it (throttled) BEFORE collecting, so
    # an agent that only ever discovers turns through this fan-out endpoint — e.g.
    # one waiting for its first match to start — still counts as LIVE. Without this,
    # last_polled_at never advances on the discovery path, provider_readiness never
    # reaches LIVE, and a held seat's connect page waits forever.
    await mark_polled(db, connection, now=now)
    candidates, ctx = await _collect_candidates(db, connection, now)
    ordered = sorted(
        candidates,
        key=lambda cand: (cand.deadline, cand.match_id, cand.round, cand.turn, cand.agent_id),
    )
    claimed = [cand for cand in ordered if await _claim_pin(db, connection, cand, ctx, now)]
    await db.commit()
    if not claimed:
        # Non-blocking fan-out: no long-poll hold here, but use the same paced
        # wait number so a per-agent loop backs off identically to get_next_turn.
        idle = await compute_idle_status(db, connection, now=now)
        _, next_poll = pace_idle(idle)
        return _idle_payload(idle, waiting_poll_hint=next_poll)
    turns = [await _build_turn_payload(db, cand, ctx, connection) for cand in claimed]
    return {"status": "your_turn", "turns": turns}
