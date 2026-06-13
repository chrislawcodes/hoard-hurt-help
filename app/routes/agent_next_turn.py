"""Connection-scoped next-turn endpoint.

The runner authenticates once per connection and receives the most urgent open
turn across all active AI agents on that connection. The payload names the
specific agent + version the turn belongs to so one runner can keep separate
sessions per agent without ever collapsing two agents in the same match.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import app.db as db_module
from app.deps import DbSession, require_connection
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
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.routes.agent_api import (
    _build_current_turn,
    _group_into_turns,
    _load_public_action_records,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Recommended re-poll cadence (seconds) the client honors via
# `next_poll_after_seconds`.
#
# `_POLL_WHEN_WAITING` is for the PLURAL `next-turns` endpoint, which returns
# immediately (no long-poll) and is what the concurrent connector polls. Raised
# from 5 to 30 so an idle connector stops burning a poll every few seconds —
# the headline idle-cost cut for the connector path.
#
# `_POLL_AFTER_LONG_POLL` is for the SINGULAR `next-turn` endpoint, which now
# bounded-long-polls: a "waiting" response only comes back AFTER the server held
# the request open for the whole window, so the wait IS the throttle. The client
# should re-open the long-poll promptly rather than sleep again on top of it.
_POLL_WHEN_WAITING = 30
_POLL_AFTER_LONG_POLL = 2

# Bounded long-poll: when no turn is open, the endpoint can HOLD the request and
# re-check instead of returning "waiting" immediately. This is opt-in per request
# via the `hold_seconds` query param, and DEFAULTS TO OFF (0.0) so the connector
# and any existing caller keep the old immediate-return behaviour and lean on
# their own `next_poll_after_seconds` sleep.
#
# Interactive MCP play (Mode A) is the caller that turns it on: the MCP
# `get_next_turn` tool proxies this endpoint with a ~25s `hold_seconds` so an idle
# game holds one request open instead of firing a fresh paid model call every few
# seconds. ~25s keeps us under typical MCP/HTTP client request timeouts
# (commonly 30s; the connector uses 40s). We re-check every
# `_LONG_POLL_INTERVAL_SECONDS` and return the instant a turn opens. Each re-check
# acquires its own DB session and releases it before the sleep — we never pin a
# connection across the wait.
_LONG_POLL_HOLD_SECONDS = 0.0  # default: off (immediate return)
_LONG_POLL_INTERVAL_SECONDS = 1.0


def _as_aware(dt: datetime) -> datetime:
    """SQLite drops timezone info on read; normalize to UTC-aware for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _agent_turn_token(turn_token: str, agent_id: int, match_id: str) -> str:
    return f"{turn_token}:{agent_id}:{match_id}"


async def _route_states(
    db: DbSession, connection: Connection
) -> tuple[dict[int, ConnectionRouteState], ConnectionRouteState]:
    """Build route states for ALL of the polling user's connections.

    Deleted connections are included (flagged dead) so a pin held by a deleted
    connection is consistently treated as dead by both the eligibility check and
    the atomic claim's dead-id set. Returns the id→state map plus the polling
    connection's own state.
    """
    conns = (
        (
            await db.execute(
                select(Connection).where(Connection.user_id == connection.user_id)
            )
        )
        .scalars()
        .all()
    )
    conn_ids = [c.id for c in conns]
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

    def _state(c: Connection) -> ConnectionRouteState:
        return ConnectionRouteState(
            connection_id=c.id,
            enabled_providers=frozenset(enabled_by_conn.get(c.id, set())),
            paused=c.status == ConnectionStatus.PAUSED,
            deleted=c.deleted_at is not None,
            last_seen_at=c.last_seen_at,
        )

    by_id = {c.id: _state(c) for c in conns}
    polling = by_id.get(connection.id) or _state(connection)
    return by_id, polling


@dataclass
class _RouteContext:
    """Lookups shared between candidate selection, pin-claiming, and payload
    rendering for one poll. Built once by _collect_candidates so the singular
    next-turn and plural next-turns endpoints stay in exact lockstep."""

    agent_by_id: dict[int, Agent]
    player_by_key: dict[tuple[int, str], Player]
    version_by_agent_id: dict[int, AgentVersion]
    latest_turn_by_match: dict[str, Turn]
    dead_ids: list[int]


async def _collect_candidates(
    db: DbSession, connection: Connection, now: datetime
) -> tuple[list[TurnCandidate], _RouteContext]:
    """Gather every open turn this connection may serve, plus the lookups needed
    to claim and render each. DB-touching counterpart to select_next_turn."""
    connections_by_id, polling_state = await _route_states(db, connection)

    agent_rows = (
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
                Match.state == GameState.ACTIVE,
            )
        )
    ).all()

    latest_turn_by_match: dict[str, Turn] = {}
    player_by_key: dict[tuple[int, str], Player] = {}
    agent_by_id: dict[int, Agent] = {}
    version_by_agent_id: dict[int, AgentVersion] = {}
    dead_ids = [
        cid
        for cid, state in connections_by_id.items()
        if connection_is_dead(state, now=now)
    ]
    ctx = _RouteContext(
        agent_by_id=agent_by_id,
        player_by_key=player_by_key,
        version_by_agent_id=version_by_agent_id,
        latest_turn_by_match=latest_turn_by_match,
        dead_ids=dead_ids,
    )
    if not agent_rows:
        return [], ctx

    for agent, player, match, version in agent_rows:
        if version is None:
            # An AI agent with no current version cannot play (no model/strategy
            # to drive a turn). Skip it VISIBLY rather than letting an INNER JOIN
            # drop it silently — a silently-skipped agent is the freeze class.
            logger.warning(
                "next-turn: agent %s (connection %s) has no current version; skipping",
                agent.id,
                connection.id,
            )
            continue
        if agent.provider is None:
            # Routing reads the stored provider; a kind=AI agent must have one
            # (CHECK constraint). Guard defensively and skip rather than crash.
            logger.warning(
                "next-turn: AI agent %s has no provider; skipping", agent.id
            )
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
            # This connection doesn't cover the agent's provider, or the match is
            # stickily pinned to another still-live connection. Leave it for the
            # connection that owns the pin.
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
                deadline=_as_aware(turn.deadline_at),
                agent_id=agent_id,
            )
        )
    return candidates, ctx


async def _claim_pin(
    db: DbSession, connection: Connection, cand: TurnCandidate, ctx: _RouteContext, now: datetime
) -> bool:
    """Atomic sticky-pin claim for one candidate. Returns True iff THIS poll won
    the pin. The WHERE clause re-checks the sticky rule (pin free, ours, or held
    by a dead connection) so two concurrent polls can't both serve the same turn
    — exactly one UPDATE affects the row. The caller commits (after one claim or
    a batch of them)."""
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
                    Player.served_by_connection_id.in_(ctx.dead_ids)
                    if ctx.dead_ids
                    else false(),
                ),
            )
            .values(served_by_connection_id=connection.id, served_pinned_at=now)
        ),
    )
    return claim.rowcount == 1


async def _build_turn_payload(
    db: DbSession, cand: TurnCandidate, ctx: _RouteContext
) -> dict[str, object]:
    """Render the full your_turn payload for a candidate whose pin we hold."""
    agent = ctx.agent_by_id[cand.agent_id]
    player = ctx.player_by_key[(cand.agent_id, cand.match_id)]
    version = ctx.version_by_agent_id[cand.agent_id]
    match = (
        await db.execute(select(Match).where(Match.id == cand.match_id))
    ).scalar_one()
    turn = ctx.latest_turn_by_match[cand.match_id]
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == match.id))).scalars().all()
    )
    seat_name_by_agent_id = {p.agent_id: p.seat_name for p in all_players}
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
    # Attach the operator's sideline note when it's targeted at this round.
    if player.coach_note and player.coach_note_round == match.current_round:
        static["coach_note"] = player.coach_note
    current = await _build_current_turn(db, turn)
    return {
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
        "agent_turn_token": _agent_turn_token(turn.turn_token, agent.id, match.id),
        "static": static,
        "history": history,
        "scoreboard": scoreboard,
        "current": current,
    }


async def _serve_one_turn(
    db: AsyncSession, connection: Connection, now: datetime
) -> dict[str, object] | None:
    """One non-blocking check: claim and render the most urgent servable turn, or
    return None when there's nothing to serve right now. Commits on a win;
    rolls back a lost race. Does NOT sleep — the long-poll loop owns the waiting.
    """
    candidates, ctx = await _collect_candidates(db, connection, now)
    chosen = select_next_turn(candidates)
    if chosen is None:
        return None
    if not await _claim_pin(db, connection, chosen, ctx, now):
        # Another live connection claimed this match first; treat as nothing to
        # serve this check and let the caller re-poll.
        await db.rollback()
        return None
    await db.commit()
    return await _build_turn_payload(db, chosen, ctx)


@router.get("/next-turn", response_model=None)
async def next_turn(
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
    hold_seconds: float = _LONG_POLL_HOLD_SECONDS,
    interval_seconds: float = _LONG_POLL_INTERVAL_SECONDS,
) -> dict[str, object]:
    """Return the single most urgent open turn this connection may serve.

    Routing is no longer connection-scoped: an agent belongs to a user, not a
    connection. This connection may serve a turn when it is the same user's, the
    agent's provider is enabled here, and the match's sticky pin is free, ours,
    or held by a now-dead connection (failover). The pin is claimed with one
    atomic conditional UPDATE so two concurrent polls can't double-serve.

    Optional bounded long-poll (opt-in via ``hold_seconds`` > 0; OFF by default):
    if no turn is open we don't return "waiting" right away — we hold the request
    up to ``hold_seconds``, re-checking every ``interval_seconds``, and return the
    instant a turn opens. This collapses a tight idle polling loop (one paid model
    call every few seconds in interactive MCP mode) into one held request per
    window. Each re-check after the first uses its OWN short-lived DB session that
    is released before the sleep — we never pin a connection across the wait. The
    connector does NOT pass ``hold_seconds`` (immediate return as before); the MCP
    ``get_next_turn`` tool passes ``MCP_LONG_POLL_HOLD_SECONDS`` so Mode A is cheap.

    The connection auth (``require_connection``, which bumps the usage counter and
    heartbeat) runs once per request via the injected ``db`` — not on every
    re-check — so holding the request open does not inflate the call count.
    """
    held = max(0.0, hold_seconds) > 0.0
    # When we held the request open, the wait WAS the throttle — tell the client to
    # re-open promptly. Otherwise (immediate return) advise the normal idle cadence.
    waiting_poll_hint = _POLL_AFTER_LONG_POLL if held else _POLL_WHEN_WAITING
    deadline = asyncio.get_event_loop().time() + max(0.0, hold_seconds)

    # First check reuses the request-scoped session (auth already opened it).
    served = await _serve_one_turn(db, connection, datetime.now(timezone.utc))
    if served is not None:
        return served

    # Nothing to serve yet. Capture the primitive id and release the request-scoped
    # session's DB connection back to the pool BEFORE we start waiting — the read
    # above left a connection checked out, and holding it idle across a ~25s
    # long-poll would pin a pooled connection for the whole window (the exact thing
    # the long-poll must avoid). After this rollback the `connection` ORM object's
    # attributes are expired, so we re-load it fresh inside each check session
    # rather than touch the detached object.
    connection_id = connection.id
    await db.rollback()

    # Hold the request, re-checking with fresh short-lived sessions that are each
    # opened, used, and closed (connection returned to the pool) inside the loop
    # body — so no DB connection is held across any sleep.
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
            # Re-validate the same gates `require_connection` enforces once at
            # request start. Without this the hold can keep serving turns for up
            # to the whole window after the connection is revoked or the owning
            # account is disabled mid-hold. On any of these, stop holding and let
            # the client re-poll — the next request hits the auth gate (410/403).
            #   - row gone: connection vanished entirely.
            #   - deleted_at set: soft-deleted (the row still exists, so the
            #     `fresh is None` check above never catches this case).
            #   - PAUSED: operator paused this connection.
            #   - owning user disabled: the account-disable guarantee.
            if (
                fresh is None
                or fresh.deleted_at is not None
                or fresh.status == ConnectionStatus.PAUSED
                or fresh.user.disabled_at is not None
            ):
                break
            served = await _serve_one_turn(check_db, fresh, datetime.now(timezone.utc))
        if served is not None:
            return served

    return {"status": "waiting", "next_poll_after_seconds": waiting_poll_hint}


@router.get("/next-turns", response_model=None)
async def next_turns(
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> dict[str, object]:
    """Return EVERY open turn this connection may serve right now, so the runner
    can drive its agents concurrently instead of one model call at a time.

    The single-turn next-turn endpoint hands back only the most urgent turn and
    keeps returning it until it resolves, which forces the runner to play matches
    serially — slow model calls then make it miss deadlines in every other match.
    This endpoint claims the sticky pin for each servable turn (same atomic
    conditional UPDATE, one per player) and renders all of them. Turns whose pin
    a live connection already holds are simply omitted from this poll's batch.
    """
    now = datetime.now(timezone.utc)
    candidates, ctx = await _collect_candidates(db, connection, now)
    # Claim in urgency order so that when two connections race, the same
    # deterministic subset lands with each — mirrors select_next_turn's tie-break.
    ordered = sorted(
        candidates,
        key=lambda c: (c.deadline, c.match_id, c.round, c.turn, c.agent_id),
    )
    claimed = [c for c in ordered if await _claim_pin(db, connection, c, ctx, now)]
    await db.commit()
    if not claimed:
        return {"status": "waiting", "next_poll_after_seconds": _POLL_WHEN_WAITING}
    turns = [await _build_turn_payload(db, c, ctx) for c in claimed]
    return {"status": "your_turn", "turns": turns}


class _ReportPidRequest(BaseModel):
    pid: int
    # Optional so OLD connectors that send only {"pid": ...} keep working
    # (acceptance #7). When present, lists the provider CLIs the connector found
    # installed on this machine (e.g. ["claude", "openai"]).
    detected_providers: list[str] | None = None
    # The machine's hostname. Used only as a DEFAULT name when the operator
    # didn't name the connection — a typed name always wins.
    hostname: str | None = None


async def _apply_detected_providers(
    db: DbSession, connection: Connection, detected: list[str]
) -> None:
    """Update connection_providers.detected from the connector's CLI sweep.

    Touches only the informational `detected`/`detected_detail` columns — NEVER
    `enabled` (the user's toggle is sacred). Creates a detected row if none
    exists yet (enabled defaults False). Providers the connector no longer
    reports are marked detected=False but left enabled as the user set them.
    """
    detected_values = {p.strip() for p in detected if p.strip()}
    rows = (
        (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == connection.id
                )
            )
        )
        .scalars()
        .all()
    )
    seen: set[str] = set()
    for row in rows:
        is_detected = row.provider.value in detected_values
        row.detected = is_detected
        row.detected_detail = "CLI detected" if is_detected else "not found"
        seen.add(row.provider.value)
    for value in detected_values - seen:
        try:
            provider = ConnectionProvider(value)
        except ValueError:
            # Connector reported a provider this server doesn't know; ignore it
            # rather than crash the best-effort startup call.
            continue
        db.add(
            ConnectionProviderRow(
                connection_id=connection.id,
                provider=provider,
                enabled=False,
                detected=True,
                detected_detail="CLI detected",
            )
        )


@router.post("/report-pid", status_code=204)
async def report_pid(
    body: _ReportPidRequest,
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> None:
    """Store the runner's OS process ID and (optionally) its detected providers."""
    connection.runner_pid = body.pid
    if body.detected_providers is not None:
        await _apply_detected_providers(db, connection, body.detected_providers)
    # Default the connection name to the machine's hostname, but never override a
    # name the operator typed.
    if connection.nickname is None and body.hostname and body.hostname.strip():
        connection.nickname = body.hostname.strip()[:60]
    await db.commit()
