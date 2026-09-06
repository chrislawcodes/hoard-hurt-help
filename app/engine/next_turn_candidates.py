"""Which of a connection's seats have a turn to serve.

Split out of ``agent_play_next_turn`` (which composes this with turn-payload
building and idle pacing to answer "what do I do next"). This module answers
one question on its own: gather every open turn the polling connection is
routed to and allowed to claim, filtering out seats that have nothing left to
do or are not on the clock yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.engine.agent_play_reads import load_open_turns
from app.engine.agent_playability import playable_agent_filter
from app.engine.next_turn import TurnCandidate
from app.engine.turn_routing import (
    ConnectionRouteState,
    TurnPin,
    can_connection_claim_turn,
    connection_is_dead,
)
from app.games import get as get_game_module
from app.games.base import GameModule
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission

logger = logging.getLogger(__name__)

# One of the connection's seats paired with its match's open turn.
_SeatRow = tuple[int, str, Player, Turn]


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
    match_by_id: dict[str, Match] = field(default_factory=dict)
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

    Restricted to the user's active AI agents seated in active matches. The
    version is the seat's pinned ``Player.agent_version_id`` (re-stamped from
    the agent's current version when the match went ACTIVE), not the agent's
    live current pointer — so a mid-match edit or restore never changes what a
    running match is served. When ``agent_id`` is given, restrict to that single
    agent so a caller running one parallel loop per agent only ever sees (and
    claims) its own agent's turn.
    """
    agents_stmt = (
        select(Agent, Player, Match, AgentVersion)
        .join(Player, Player.agent_id == Agent.id)
        .join(Match, Match.id == Player.match_id)
        .join(AgentVersion, AgentVersion.id == Player.agent_version_id, isouter=True)
        .where(
            Agent.user_id == connection.user_id,
            *playable_agent_filter(),
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
                "next-turn: agent %s (connection %s) has no pinned version"
                " for match %s; skipping",
                agent.id,
                connection.id,
                match.id,
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
        ctx.match_by_id[match.id] = match
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


async def _drop_seats_off_the_clock(
    db: AsyncSession, ctx: CandidateContext, seats: list[_SeatRow]
) -> list[_SeatRow]:
    """Drop the seats a sequential game would refuse a move from.

    A simultaneous game (PD) resolves every seat each turn, so every seat owes a
    move and nothing is dropped. Its module is never even asked: ``next_actor``
    is deliberately fail-loud for simultaneous games, so consulting it on the PD
    path would turn every PD poll into a 500.

    A sequential game (Liar's Dice) has exactly one seat on the clock. The others
    cannot submit — ``validate_move`` answers them NOT_YOUR_TURN — so serving one
    buys a paid model think and a 400, and leaves the seat a candidate again on
    the very next poll. Worse, a user holding two seats in the same match can
    have the idle one win ``select_next_turn``'s tie-break and starve the seat
    that actually owes the move until its deadline defaults it.

    The question is put to ``next_actor``, the same hook the sequential driver
    uses to choose whose turn to open and whose submission to wait for, so
    serving and the turn loop cannot disagree about whose move it is.
    """
    modules_by_match: dict[str, GameModule] = {}
    for _agent_id, match_id, _player, _turn in seats:
        if match_id in modules_by_match:
            continue
        module = get_game_module(ctx.match_by_id[match_id].game)
        if not module.config_defaults().simultaneous:
            modules_by_match[match_id] = module
    if not modules_by_match:
        return seats

    # One `next_actor` call per sequential match in the poll, not per seat.
    actor_by_match = {
        match_id: await module.next_actor(db, ctx.match_by_id[match_id])
        for match_id, module in modules_by_match.items()
    }
    kept: list[_SeatRow] = []
    for seat in seats:
        _agent_id, match_id, player, _turn = seat
        if match_id in actor_by_match and player.seat_name != actor_by_match[match_id]:
            continue
        kept.append(seat)
    return kept


async def _filter_to_candidates(
    db: AsyncSession, ctx: CandidateContext
) -> list[TurnCandidate]:
    """Keep only the open turns the player still owes a move on.

    Drops a turn the player already acted on, and — during the talk phase — one
    the player already broadcast a message for, since there is nothing left to do
    until the act phase opens. In a sequential game it also drops every seat but
    the one on the clock (see ``_drop_seats_off_the_clock``).
    """
    seats: list[_SeatRow] = []
    for (agent_id, match_id), player in ctx.player_by_key.items():
        turn = ctx.latest_turn_by_match.get(match_id)
        if turn is None:
            continue
        seats.append((agent_id, match_id, player, turn))
    seats = await _drop_seats_off_the_clock(db, ctx, seats)
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
