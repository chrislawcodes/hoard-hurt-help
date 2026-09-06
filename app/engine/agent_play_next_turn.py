"""Connection-level "what do I do next" fan-out for the agent-play service.

A single connection may drive several agents across several active matches. This
is the entry point: it composes candidate collection (``next_turn_candidates``),
turn claiming and payload building (``next_turn_payload``), and idle pacing
(``agent_idle``) into ``get_next_turn`` / ``get_next_turns``. Agent identity for
the MCP instructions flow lives in ``next_turn_identity`` — a different question
from turn serving, so it is not composed here. This layer sits above
``agent_play_reads`` and ``agent_play_guards``; the per-match verbs do not
import it and it does not import them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as db_module
from app.clamp import clamp
from app.engine.agent_idle import (
    LONG_POLL_INTERVAL_SECONDS,
    IdleStatus,
    compute_idle_status,
    pace_idle,
)
from app.engine.connection_activity import mark_polled, mark_still_holding
from app.engine.connection_auth_loading import connection_user_load_options
from app.engine.next_turn import select_next_turn
from app.engine.next_turn_candidates import _collect_candidates
from app.engine.next_turn_payload import _build_turn_payload, _claim_pin
from app.models.connection import Connection, ConnectionStatus
from app.ops_events import log_ops_event

logger = logging.getLogger(__name__)


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
    # One line per turn actually handed to a client. This is the only record of
    # WHO got served WHAT — the seat's served_by_connection_id is per-seat, not
    # per-turn, so it cannot answer "was this turn served twice?".
    #
    # Finding a double-serve: two lines sharing the same agent_id + match_id +
    # round + turn. That happens when a user runs two client sessions for one
    # agent — the claim in _claim_pin only excludes OTHER connections, and every
    # session from one client shares a connection, so both sessions pass it and
    # both pay for a full model think on the same turn.
    log_ops_event(
        logger,
        logging.INFO,
        "turn_served",
        f"served round {chosen.round} turn {chosen.turn} to agent {chosen.agent_id}",
        connection_id=connection.id,
        agent_id=chosen.agent_id,
        match_id=chosen.match_id,
        round=chosen.round,
        turn=chosen.turn,
        pinned_agent_id=agent_id if agent_id is not None else "-",
    )
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

    # Every idle poll is a paid model call on the client side, so this is the
    # record of what the server asked it to do. DEBUG because it fires on every
    # poll — turn it on when investigating cost or a session that stopped, and
    # read `next_poll` as "seconds we told a client with no timer to wait".
    log_ops_event(
        logger,
        logging.DEBUG,
        "turn_poll_idle",
        f"no turn to serve; hold {hold_seconds:.0f}s then wait {next_poll}s",
        connection_id=connection.id,
        pinned_agent_id=agent_id if agent_id is not None else "-",
        has_game=idle.has_game,
        hold_seconds=round(hold_seconds, 1),
        next_poll_after_seconds=next_poll,
        should_stop=idle.should_stop,
    )

    if hold_seconds <= 0.0:
        await db.rollback()
        return _idle_payload(idle, waiting_poll_hint=next_poll)

    connection_id = connection.id
    await db.rollback()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + hold_seconds
    # One session for the whole hold — no repeated open/close per tick — but the
    # transaction is closed after every tick (see the rollback below), so the
    # pooled DB connection is only checked out while a tick is actually querying.
    # populate_existing forces each re-query to reflect the live DB row even
    # though the identity map has the Connection from earlier in this session.
    async with db_module.SessionLocal() as check_db:
        while loop.time() < deadline:
            await asyncio.sleep(
                clamp(deadline - loop.time(), 0.0, LONG_POLL_INTERVAL_SECONDS)
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
            # The agent is waiting on US, so keep its liveness stamps fresh. Left
            # alone they age for the whole hold, and past 90s the connection reads
            # as dead for turn routing — it would be refused its own turn by the
            # very hold that is waiting to serve it. Throttled; see the docstring
            # for why this is not `mark_seen`.
            await mark_still_holding(check_db, fresh)
            served = await _serve_one_turn(
                check_db, fresh, datetime.now(timezone.utc), agent_id=agent_id
            )
            if served is not None:
                return served
            # Nothing to serve this tick. End the transaction before sleeping
            # again: a session holding an open transaction keeps its pooled DB
            # connection checked out, so without this every caller inside a hold
            # pins one connection for the hold's full duration and the pool runs
            # dry once a handful of agents are waiting at once. `_serve_one_turn`
            # leaves the read transaction open when it finds no candidate; the
            # rollback is a no-op on the path where it already rolled back.
            await check_db.rollback()

    # The hold ended with nothing to serve. Rebuild the reply from a FRESH idle
    # picture rather than reusing the one computed before the hold: that one is
    # stale by the hold's length, and — once the idle lanes hold — reusing the
    # bare "waiting" shape would drop `should_stop` entirely. The pasted play
    # prompt's only instruction to stop is "stop when should_stop is true", so a
    # reply that cannot carry it makes the loop unstoppable.
    async with db_module.SessionLocal() as after_db:
        fresh_connection = await after_db.get(Connection, connection_id)
        if fresh_connection is None:
            return {"status": "waiting", "next_poll_after_seconds": next_poll}
        after_idle = await compute_idle_status(
            after_db, fresh_connection, agent_id=agent_id
        )
        _, after_poll = pace_idle(after_idle)
        return _idle_payload(after_idle, waiting_poll_hint=after_poll)


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
        # Non-blocking fan-out: this endpoint never holds, so it must keep the
        # original wait numbers (`can_hold=False`). The always-on connector polls
        # here and sleeps the number we hand back; giving it the in-play 5s would
        # multiply its request rate ~12x forever on a client we cannot update.
        idle = await compute_idle_status(db, connection, now=now)
        _, next_poll = pace_idle(idle, can_hold=False)
        return _idle_payload(idle, waiting_poll_hint=next_poll)
    turns = [await _build_turn_payload(db, cand, ctx) for cand in claimed]
    return {"status": "your_turn", "turns": turns}
