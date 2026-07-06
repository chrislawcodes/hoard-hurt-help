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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import Row, false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as db_module
from app.aware_datetime import ensure_aware
from app.engine.agent_idle import (
    LONG_POLL_INTERVAL_SECONDS,
    IdleStatus,
    compute_idle_status,
    pace_idle,
)
from app.engine.connection_activity import mark_polled
from app.engine.connection_auth_loading import connection_user_load_options
from app.engine.agent_play_reads import (
    RECENT_HISTORY_TURNS,
    _build_current_turn,
    _group_into_turns,
    _load_public_action_records,
    build_public_scoreboard_dicts,
    build_turn_static_dict,
    load_match_players,
    load_open_turns,
    sorted_seat_names,
)
from app.engine.model_provider_match import resolve_seat_model
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

logger = logging.getLogger(__name__)

# One (agent, player, match, version) row of the connection's in-play seats; the
# version is optional because the AgentVersion join is an outer join.
_AgentMatchRow = tuple[Agent, Player, Match, AgentVersion | None]


@dataclass
class CandidateContext:
    """Lookups gathered while collecting candidates, reused to claim and serve.

    ``_collect_candidates`` fills these once; ``_claim_pin`` and
    ``_build_turn_payload`` read them back without a second query. The maps are
    keyed exactly as the original context dict was, so claim and payload behavior
    is unchanged.
    """

    agent_by_id: dict[int, Agent] = field(default_factory=dict)
    player_by_key: dict[tuple[int, str], Player] = field(default_factory=dict)
    version_by_agent_id: dict[int, AgentVersion] = field(default_factory=dict)
    latest_turn_by_match: dict[str, Turn] = field(default_factory=dict)
    dead_ids: list[int] = field(default_factory=list)


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


async def _fetch_active_agent_rows(
    db: AsyncSession,
    connection: Connection,
    *,
    agent_id: int | None,
) -> list[Row[tuple[Agent, Player, Match, AgentVersion]]]:
    """Every (agent, player, match, version) the connection's user has in play.

    Restricted to the user's active AI agents seated in active matches. When
    ``agent_id`` is given, restrict to that single agent so a caller running one
    parallel loop per agent only ever sees (and claims) its own agent's turn.
    """
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
    return list((await db.execute(agents_stmt)).all())


async def _build_candidate_lookups(
    db: AsyncSession,
    connection: Connection,
    agent_rows: list[Row[tuple[Agent, Player, Match, AgentVersion]]],
    *,
    polling_state: ConnectionRouteState,
    connections_by_id: dict[int, ConnectionRouteState],
    now: datetime,
) -> CandidateContext:
    """Fold the agent rows into the lookup maps used to claim and serve turns.

    Only rows the polling connection is allowed to claim survive (routing + the
    sticky pin). The surviving matches' open turns are then loaded in one batched
    query rather than one round trip per match.
    """
    dead_ids = [
        cid
        for cid, state in connections_by_id.items()
        if connection_is_dead(state, now=now)
    ]
    ctx = CandidateContext(dead_ids=dead_ids)
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
        # Route by the AI the user picked for this seat: only a connection that
        # covers the seat's chosen provider may claim it. The sticky pin (handled
        # inside) still keeps a single connection serving a seat once it starts.
        # Legacy seats with no chosen provider (None) fall back to "any
        # connection" so pre-feature in-flight games keep playing.
        if not can_connection_claim_turn(
            polling_state,
            player.chosen_provider,
            pin,
            now=now,
            connections_by_id=connections_by_id,
        ):
            continue
        ctx.player_by_key[(agent.id, match.id)] = player
        ctx.agent_by_id[agent.id] = agent
        ctx.version_by_agent_id[agent.id] = version
    # Matches with no open turn are simply absent from the map (same as the old
    # per-match ``None`` skip), so the downstream ``.get`` lookups are unchanged.
    match_ids = {match_id for _agent_id, match_id in ctx.player_by_key}
    ctx.latest_turn_by_match = dict(await load_open_turns(db, list(match_ids)))
    return ctx


async def _undefaulted_pairs(
    db: AsyncSession,
    model: type[TurnSubmission] | type[TurnMessage],
    turn_ids: set[int],
    player_ids: set[int],
) -> set[tuple[int, int]]:
    """(turn_id, player_id) pairs with a real (non-defaulted) row for the model.

    One helper for both the act check (TurnSubmission) and the talk check
    (TurnMessage) so the two batched reads can't drift apart in their
    predicates.
    """
    rows = await db.execute(
        select(model.turn_id, model.player_id).where(
            model.turn_id.in_(turn_ids),
            model.player_id.in_(player_ids),
            model.was_defaulted.is_(False),
        )
    )
    return {(row.turn_id, row.player_id) for row in rows.all()}


async def _filter_to_candidates(
    db: AsyncSession, ctx: CandidateContext
) -> list[TurnCandidate]:
    """Keep only the open turns the player still owes a move on.

    Drops a turn the player already acted on, and — during the talk phase — one
    the player already broadcast a message for, since there is nothing left to do
    until the act phase opens.
    """
    seats: list[tuple[int, str, Player, Turn]] = []
    for (agent_id, match_id), player in ctx.player_by_key.items():
        turn = ctx.latest_turn_by_match.get(match_id)
        if turn is None:
            continue
        seats.append((agent_id, match_id, player, turn))
    if not seats:
        return []

    # Two batched existence reads — mirroring _load_public_action_records —
    # instead of one round trip per seat. The predicates are the per-seat
    # originals verbatim (non-defaulted rows only), scoped to this connection's
    # own players so a big table doesn't inflate the fetch; per-seat membership
    # is then tested in memory on (turn_id, player_id).
    player_ids = {player.id for _agent_id, _match_id, player, _turn in seats}
    turn_ids = {turn.id for _agent_id, _match_id, _player, turn in seats}
    submitted = await _undefaulted_pairs(db, TurnSubmission, turn_ids, player_ids)
    talk_turn_ids = {
        turn.id for _agent_id, _match_id, _player, turn in seats if turn.phase == "talk"
    }
    messaged: set[tuple[int, int]] = set()
    if talk_turn_ids:
        messaged = await _undefaulted_pairs(db, TurnMessage, talk_turn_ids, player_ids)

    candidates: list[TurnCandidate] = []
    for agent_id, match_id, player, turn in seats:
        if (turn.id, player.id) in submitted:
            continue
        # Talk-phase symmetry with the act check above: a player who has already
        # broadcast their talk message has nothing left to do until the act phase
        # opens. Without this, every poll during the talk->act gap re-serves the
        # same full turn payload (entire history included), which bloats the AI's
        # context and trips client-side loop detectors. Skip it so the loop
        # long-polls and serves the act phase once, when it actually opens.
        if turn.phase == "talk" and (turn.id, player.id) in messaged:
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
    return candidates


async def _collect_candidates(
    db: AsyncSession,
    connection: Connection,
    now: datetime,
    *,
    agent_id: int | None = None,
) -> tuple[list[TurnCandidate], CandidateContext]:
    connections_by_id, polling_state = await _load_route_states(db, connection)
    agent_rows = await _fetch_active_agent_rows(db, connection, agent_id=agent_id)
    ctx = await _build_candidate_lookups(
        db,
        connection,
        agent_rows,
        polling_state=polling_state,
        connections_by_id=connections_by_id,
        now=now,
    )
    candidates = await _filter_to_candidates(db, ctx)
    return candidates, ctx


async def _active_ai_agent_ids(db: AsyncSession, connection: Connection) -> list[int]:
    """The connection user's active, non-archived AI agent ids, sorted."""
    return sorted(
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


async def _identity_candidate_rows(
    db: AsyncSession, connection: Connection
) -> list[_AgentMatchRow]:
    """Every (agent, player, match, version) the user has in a live or upcoming
    match — the candidate set the identity picker ranks over.
    """
    return [
        cast(_AgentMatchRow, row)
        for row in (
            await db.execute(
                select(Agent, Player, Match, AgentVersion)
                .join(Player, Player.agent_id == Agent.id)
                .join(Match, Match.id == Player.match_id)
                .join(
                    AgentVersion,
                    AgentVersion.id == Agent.current_version_id,
                    isouter=True,
                )
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
    ]


def _rank_agent_matches(
    match_rows: list[list[_AgentMatchRow]],
    open_turns_by_match: dict[str, Turn],
) -> list[list[_AgentMatchRow]]:
    """Order the candidate matches so the most urgent one sorts first.

    Ranking key (smallest wins): active-before-upcoming, then the soonest "when"
    (an active match's open-turn deadline, else the match's scheduled start, else
    the far future), then match id as a stable final tiebreak. Open turns come
    from a single batched lookup rather than one query per match.
    """
    ranked: list[tuple[tuple[object, ...], list[_AgentMatchRow]]] = []
    for rows in match_rows:
        match = rows[0][2]
        current_turn = (
            open_turns_by_match.get(match.id)
            if match.state == GameState.ACTIVE
            else None
        )
        if current_turn is not None:
            when = ensure_aware(current_turn.deadline_at)
        elif match.scheduled_start is not None:
            when = ensure_aware(match.scheduled_start)
        else:
            when = datetime.max.replace(tzinfo=timezone.utc)
        ranked.append(
            ((0 if match.state == GameState.ACTIVE else 1, when, match.id), rows)
        )
    return [rows for _key, rows in sorted(ranked, key=lambda item: item[0])]


def _extract_agent_identity(
    selected_rows: list[_AgentMatchRow], selected_agent_id: int
) -> tuple[Match, str, list[Any], str] | None:
    """Pull the chosen agent's identity out of its match's rows, or None if the
    seat or its current version is missing.
    """
    match = selected_rows[0][2]
    your_player = next(
        (
            player
            for agent, player, _match, _version in selected_rows
            if agent.id == selected_agent_id
        ),
        None,
    )
    version = next(
        (
            version
            for agent, _player, _match, version in selected_rows
            if agent.id == selected_agent_id and version is not None
        ),
        None,
    )
    if your_player is None or version is None:
        return None
    seat_name_by_agent_id = {
        player.agent_id: player.seat_name
        for _agent, player, _match, _version in selected_rows
    }
    all_agent_ids = sorted_seat_names(seat_name_by_agent_id)
    return (
        match,
        seat_name_by_agent_id[your_player.agent_id],
        all_agent_ids,
        version.strategy_text,
    )


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
    active_agent_ids = await _active_ai_agent_ids(db, connection)
    if not active_agent_ids:
        return None, None, [], None
    if agent_id is None and len(active_agent_ids) > 1:
        return None, None, list(active_agent_ids), None

    selected_agent_id = agent_id or active_agent_ids[0]
    if selected_agent_id not in active_agent_ids:
        return None, None, [], None

    candidate_rows = await _identity_candidate_rows(db, connection)
    if not candidate_rows:
        return None, None, [], None

    rows_by_match_id: dict[str, list[_AgentMatchRow]] = {}
    for agent, player, match, version in candidate_rows:
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

    # Batch the open-turn lookup for every candidate match in one query instead of
    # one per match; only active matches consult it during ranking.
    open_turns_by_match = await load_open_turns(
        db, [rows[0][2].id for rows in match_rows]
    )
    selected_rows = _rank_agent_matches(match_rows, open_turns_by_match)[0]
    identity = _extract_agent_identity(selected_rows, selected_agent_id)
    if identity is None:
        return None, None, [], None
    return identity


async def _claim_pin(
    db: AsyncSession,
    connection: Connection,
    cand: TurnCandidate,
    ctx: CandidateContext,
    now: datetime,
) -> bool:
    dead_ids = ctx.dead_ids
    player = ctx.player_by_key[(cand.agent_id, cand.match_id)]
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
                    .options(connection_user_load_options())
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
    turns = [await _build_turn_payload(db, cand, ctx) for cand in claimed]
    return {"status": "your_turn", "turns": turns}
