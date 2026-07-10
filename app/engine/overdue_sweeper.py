"""Self-heal frozen matches: force-advance turns stuck long past their deadline.

Resume-on-startup and the poller watchdog already recover *interruptions* (the
process died mid-turn; a restarted loop picks up from current_round/current_turn).
What they cannot recover is a *deterministic* crash: bot decisions are
deterministic, so a turn loop that throws on a specific move re-throws on every
watchdog restart and the match freezes permanently (incidents M_0279, G_0012 in
docs/operations/debugging-history.md). This sweeper closes that gap.

The freeze signature (same one the debugging playbook uses): a match is ACTIVE
and its current turn has `resolved_at IS NULL` long past `deadline_at`. A healthy
loop can never look like that — `_wait_for_messages` / `_wait_for_turn` exit at
the deadline and resolution takes milliseconds — and the watchdog restarts dead
tasks every poll tick, so a turn still unresolved OVERDUE_TURN_GRACE_SECONDS
after its deadline means restarting does not help. The grace period, not task
liveness, is the discriminator: crash-looping tasks are alive most of the time.

The heal deliberately does NOT re-run the crash-prone submission path
(`auto_submit_bot_phase` was M_0279's poison). Missing input is materialized
through the same default machinery the deadline already implies:

  * talk phase stuck  -> resolver.finalize_talk_phase (defaulted messages), then
    _begin_act_phase with a fresh full act window so live players still get
    their fair turn. If the act phase then freezes too, the next sweep resolves it.
  * act phase stuck   -> module.resolve_turn, which defaults missing submissions
    via the game module's own rules (PD: HOARD) and stamps resolved_at.

After the heal the loop is restarted; the resume path skips resolved turns
(`_run_turn` early-return) and `matches.rounds_awarded` guards round re-awards,
so the sweeper never touches current_round/current_turn and never re-scores.

Scope: simultaneous drivers only. A sequential game's resolve_turn only stamps
the timestamp (state advances at record time), so force-advancing it here would
strand its game state; those matches are logged as unhealable instead. Also out
of scope: a deterministic crash inside resolve/award/finalize itself — the heal
would hit the same exception; it is recorded as an incident and the match stays
frozen until the code fix ships (the pre-sweeper status quo, minus the silence).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.aware_datetime import ensure_aware
from app.db import SessionLocal
from app.engine import resolver
from app.engine.scheduler_turn_loop import _begin_act_phase
from app.games import get as get_game_module
from app.models.match import Match, GameState
from app.models.turn import Turn
from app.ops_events import log_ops_event
from app.request_logging import record_background_incident

if TYPE_CHECKING:
    from app.engine.scheduler import SchedulerRegistry
    from app.games.base import GameModule

logger = logging.getLogger(__name__)

# How long past a turn's deadline_at before the sweeper treats the match as
# frozen rather than merely slow. Must comfortably exceed one watchdog restart
# plus a resumed loop's catch-up (both are seconds) so a recovering interruption
# is never force-advanced; 60s ≈ 30 failed restart attempts.
OVERDUE_TURN_GRACE_SECONDS = 60.0


def _past_grace(turn: Turn, now: datetime) -> bool:
    """True when the turn's deadline is at least the grace period behind ``now``.

    This is the sweeper's central discriminator between slow and frozen: a turn
    whose deadline is still within the grace window is left strictly alone. The
    comparison happens in Python via ensure_aware (not in SQL) so naive-datetime
    storage (SQLite tests) and aware storage compare identically.
    """
    cutoff = now - timedelta(seconds=OVERDUE_TURN_GRACE_SECONDS)
    return ensure_aware(turn.deadline_at) <= cutoff


async def sweep_overdue_turns(
    session_factory: async_sessionmaker | None = None,
    registry: SchedulerRegistry | None = None,
) -> int:
    """Force-advance every frozen ACTIVE match; return how many were healed.

    Runs as a poller subsystem (see SchedulerRegistry._poll_due_loop). One
    match's failed heal must not strand the others, so per-match failures are
    recorded as background incidents (queryable, greppable) and the sweep moves
    on — the failure surfaces exactly like a turn-loop crash does.
    """
    from app.engine import scheduler

    factory = session_factory or SessionLocal
    reg = registry if registry is not None else scheduler.registry

    candidates = await _find_frozen_turns(factory)
    healed = 0
    for match_id, round_num, turn_num in candidates:
        try:
            healed += await _heal_match(
                factory, reg, match_id, round_num, turn_num
            )
        except Exception as exc:
            # fail-open per match, loud like a loop crash: the incident is
            # persisted and greppable; remaining frozen matches still get swept.
            log_ops_event(
                logger,
                logging.ERROR,
                "overdue_turn_sweep_failed",
                f"sweeper could not heal match {match_id} at R{round_num}T{turn_num}",
                match_id=match_id,
                round=round_num,
                turn=turn_num,
                error_type=type(exc).__name__,
            )
            await record_background_incident(
                source="scheduler:sweep_overdue_turns",
                exc=exc,
                match_id=match_id,
                stage="overdue_sweep",
                context={"round": round_num, "turn": turn_num},
            )
    return healed


async def _find_frozen_turns(
    factory: async_sessionmaker,
) -> list[tuple[str, int, int]]:
    """Return (match_id, round, turn) for every ACTIVE match frozen past grace.

    The overdue check is ``_past_grace`` (in Python, not SQL). The
    unresolved-turns-of-active-matches set is tiny, so this is cheap.
    """
    now = datetime.now(timezone.utc)
    frozen: list[tuple[str, int, int]] = []
    async with factory() as db:
        rows = (
            await db.execute(
                select(Turn, Match)
                .join(Match, Match.id == Turn.match_id)
                .where(
                    Match.state == GameState.ACTIVE,
                    Turn.resolved_at.is_(None),
                )
            )
        ).all()
        for turn, match in rows:
            if not _past_grace(turn, now):
                continue  # slow, not frozen — hands off before deadline+grace
            if (turn.round, turn.turn) != (match.current_round, match.current_turn):
                # An unresolved turn that is not the match's current pointer is
                # an anomaly the resume path would not walk past — surface it
                # rather than force-advance state we do not understand.
                log_ops_event(
                    logger,
                    logging.ERROR,
                    "overdue_turn_pointer_mismatch",
                    f"match {match.id} has overdue unresolved turn"
                    f" R{turn.round}T{turn.turn} but pointer is at"
                    f" R{match.current_round}T{match.current_turn} — not sweeping",
                    match_id=match.id,
                    round=turn.round,
                    turn=turn.turn,
                )
                continue
            frozen.append((match.id, turn.round, turn.turn))
    return frozen


async def _heal_match(
    factory: async_sessionmaker,
    reg: SchedulerRegistry,
    match_id: str,
    round_num: int,
    turn_num: int,
) -> int:
    """Stop the match's loop task, force-advance its stuck phase, restart it.

    Returns 1 if a heal was applied, 0 if the match no longer needed one.
    Everything is re-checked in a fresh session after the task is stopped, so a
    turn the loop resolved between detection and heal is left alone (idempotent).
    """
    from app.engine import scheduler

    # Cancel the (possibly crash-looping or wedged) task before touching the
    # turn, so the sweeper and the loop never drive the same turn concurrently.
    # CancelledError is not an Exception, so stopping never records a bogus
    # turn_loop_crashed incident.
    reg.stop(match_id)

    async with factory() as db:
        state = await _load_frozen_state(db, match_id, round_num, turn_num)
        if state is None:
            reg.start(match_id)
            return 0
        match, turn, module = state

        if turn.talk_resolved_at is None:
            # Stuck in talk: default the missing messages and open a fresh, full
            # act window — live players keep their fair act phase. The restarted
            # loop re-publishes the act turn_opened itself on resume.
            await resolver.finalize_talk_phase(db, turn)
            await _begin_act_phase(db, match, turn)
            await scheduler.publish(
                match_id, "turn_talked", {"round": round_num, "turn": turn_num}
            )
            action = "talk_defaulted_act_reopened"
        else:
            if turn.phase != "act":
                # Crash landed between talk resolution and the phase flip;
                # normalize so the resumed loop sees a coherent act phase.
                await _begin_act_phase(db, match, turn)
            # Stuck in act: resolve through the module, which materializes each
            # missing submission as the game's default move (PD: HOARD) — never
            # through auto_submit_bot_phase, whose decision path is exactly what
            # a deterministic crash poisons (M_0279).
            await module.resolve_turn(db, turn)
            await scheduler.publish(
                match_id, "turn_resolved", {"round": round_num, "turn": turn_num}
            )
            action = "act_defaulted_turn_resolved"

    log_ops_event(
        logger,
        logging.WARNING,
        "overdue_turn_swept",
        f"force-advanced frozen match {match_id} at R{round_num}T{turn_num}"
        f" ({action})",
        match_id=match_id,
        round=round_num,
        turn=turn_num,
        action=action,
    )
    # Resume the loop: it skips the now-resolved turn (_run_turn early-return)
    # and continues; round awards stay single-shot via matches.rounds_awarded.
    reg.start(match_id)
    return 1


async def _load_frozen_state(
    db: AsyncSession, match_id: str, round_num: int, turn_num: int
) -> tuple[Match, Turn, GameModule] | None:
    """Re-verify the freeze in a fresh session; None means nothing to heal."""
    match = (
        await db.execute(select(Match).where(Match.id == match_id))
    ).scalar_one_or_none()
    if match is None or match.state != GameState.ACTIVE:
        return None
    turn = (
        await db.execute(
            select(Turn).where(
                Turn.match_id == match_id,
                Turn.round == round_num,
                Turn.turn == turn_num,
            )
        )
    ).scalar_one_or_none()
    if turn is None or turn.resolved_at is not None:
        return None
    if not _past_grace(turn, datetime.now(timezone.utc)):
        return None
    module = get_game_module(match.game)
    if not module.config_defaults().simultaneous:
        # A sequential game's resolve_turn only stamps resolved_at (state
        # advances at record time), so force-advancing here would strand its
        # game state. Surface the freeze loudly instead of guessing.
        log_ops_event(
            logger,
            logging.ERROR,
            "overdue_turn_unhealable",
            f"match {match_id} (game {match.game!r}) is frozen at"
            f" R{round_num}T{turn_num} but uses a sequential driver — the"
            " sweeper cannot force-advance it; manual recovery required",
            match_id=match_id,
            round=round_num,
            turn=turn_num,
            game_type=match.game,
        )
        return None
    return match, turn, module
