"""Per-game asyncio scheduler: registry of running tasks + due-game poller.

For each ACTIVE game, a `_run_game` task runs (the per-match turn loop now lives
in `scheduler_turn_loop.py`):
  for each round 1..N:
    reset current_round_score on all players to 0
    for each turn 1..M:
      open a Turn row, broadcast 'turn_opened'
      wait_until(deadline_at)
      resolve_turn(); broadcast 'turn_resolved'
    award_round_winners; broadcast 'round_ended'
  finalize_game; broadcast 'game_completed'

A SchedulerRegistry tracks the running task per game so we can start
new ones and resume after process restarts.

The turn-loop entry points (`_run_game`, `_run_game_guarded`) and helpers
(`_open_turn`, drivers, ...) are re-exported from `scheduler_turn_loop` so the
rest of the engine and the test suite can keep referencing them at
`app.engine.scheduler`. The dependency is one-directional: this module imports
the turn loop, never the reverse at module load.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.aware_datetime import ensure_aware
from app.broadcast import publish
from app.db import SessionLocal
from app.engine.bots.service import auto_submit_bot_phase
from app.engine.match_cancellation import mark_cancelled
from app.engine.player_counts import active_player_count
from app.engine.scheduler_turn_loop import (
    SimultaneousDriver,
    _all_messaged,
    _all_submitted,
    _begin_act_phase,
    _open_turn,
    _run_game,
    _run_game_guarded,
    _select_driver,
    _wait_for_messages,
    _wait_for_turn,
)
from app.engine.state_machine import assert_transition
from app.models.match import Match, GameState
from app.ops_events import log_ops_event
from app.request_logging import record_background_incident

# Re-exported turn-loop symbols (defined in scheduler_turn_loop) — kept in
# __all__ so tooling sees them as part of this module's public surface and
# linters don't flag the imports as unused.
__all__ = [
    "MIN_PLAYERS_TO_START",
    "SchedulerRegistry",
    "SimultaneousDriver",
    "_all_messaged",
    "_all_submitted",
    "_begin_act_phase",
    "_open_turn",
    "_run_game",
    "_run_game_guarded",
    "_select_driver",
    "_wait_for_messages",
    "_wait_for_turn",
    "auto_submit_bot_phase",
    "cancel_overdue_unfilled_games",
    "publish",
    "record_background_incident",
    "registry",
    "start_game",
]

logger = logging.getLogger(__name__)

# How often the background poller checks for games that are due to start.
_START_POLL_SECONDS = 2.0
# Hard floor of players to actually run a game. `min_players` on a game is a
# SOFT lobby target (what the admin advertises); this is the rules-mechanical
# minimum. A due game with fewer than this is cancelled rather than left stuck.
MIN_PLAYERS_TO_START = 3
# After this many consecutive failures of the same poller subsystem, escalate
# the log from `exception` to `critical` — a persistently broken subsystem must
# not just re-log the same error forever with no alarm.
_POLLER_ESCALATE_AFTER = 5


async def _active_player_count(db: AsyncSession, match_id: str) -> int:
    """Confirmed seats in a game (the start floor).

    Delegates to the shared count helper: a player who left frees their seat,
    and a *held* seat (join-before-connect, ``seat_reserved_until`` set) is not a
    real player yet, so neither counts toward the start floor.
    """
    return await active_player_count(db, match_id, exclude_reserved=True)


class SchedulerRegistry:
    """Singleton-ish registry of running per-game tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._poller: asyncio.Task | None = None
        # Consecutive-failure count per poller subsystem name. Reset to 0 on a
        # successful run; escalates to logger.critical past _POLLER_ESCALATE_AFTER.
        self._subsystem_failures: dict[str, int] = {}

    def is_running(self, match_id: str) -> bool:
        t = self._tasks.get(match_id)
        return t is not None and not t.done()

    def start(self, match_id: str) -> None:
        if self.is_running(match_id):
            return
        task = asyncio.create_task(_run_game_guarded(match_id))
        # The loop is fire-and-forget. The guarded wrapper logs crashes, and the
        # done-callback still retrieves the exception so Python doesn't emit an
        # unretrieved-task warning when the task dies.
        task.add_done_callback(functools.partial(self._log_task_result, match_id))
        self._tasks[match_id] = task

    def _log_task_result(self, match_id: str, task: asyncio.Task) -> None:
        """Log a game loop that ended in an exception (not a clean finish)."""
        if task.cancelled():
            return
        # Retrieving the exception prevents the "Task exception was never
        # retrieved" warning when the guarded task has already logged it.
        task.exception()

    def stop(self, match_id: str) -> None:
        t = self._tasks.pop(match_id, None)
        if t and not t.done():
            t.cancel()

    async def start_due_games(self, session_factory: async_sessionmaker | None = None) -> int:
        """Resolve every game whose scheduled_start has passed.

        Start it if it has at least MIN_PLAYERS_TO_START players (the hard
        floor); otherwise cancel it. `min_players` on the game is a soft lobby
        target and is NOT used as a gate — a game must never sit past its start
        time unresolved. Returns how many games were started.
        """
        factory = session_factory or SessionLocal
        started = 0
        async with factory() as db:
            now = datetime.now(timezone.utc)
            games = (
                (
                    await db.execute(
                        select(Match).where(
                            Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING])
                        )
                    )
                )
                .scalars()
                .all()
            )
            for g in games:
                if ensure_aware(g.scheduled_start) > now:
                    continue  # not due yet
                count = await _active_player_count(db, g.id)
                if count >= MIN_PLAYERS_TO_START:
                    await start_game(db, g)
                    started += 1
                    logger.info("auto-started %s with %d players", g.id, count)
                else:
                    mark_cancelled(g, now)
                    await db.commit()
                    logger.info(
                        "auto-cancelled %s: %d players at start time (< %d)",
                        g.id,
                        count,
                        MIN_PLAYERS_TO_START,
                    )
        return started

    def start_poller(self, session_factory: async_sessionmaker | None = None) -> None:
        """Begin the background loop that auto-starts due games."""
        if self._poller is not None and not self._poller.done():
            return
        self._poller = asyncio.create_task(self._poll_due_loop(session_factory))

    def stop_poller(self) -> None:
        if self._poller is not None and not self._poller.done():
            self._poller.cancel()

    async def _run_subsystem(
        self, name: str, coro_factory: Callable[[], Awaitable[object]]
    ) -> None:
        """Run one poller subsystem, never letting it kill the poller.

        Each failure logs with `logger.exception` and bumps a consecutive-failure
        counter for `name`. Once a subsystem fails `_POLLER_ESCALATE_AFTER` times
        in a row, it also logs `logger.critical` so a persistently broken
        subsystem raises an alarm instead of silently re-logging forever. A
        success resets the counter.

        `coro_factory` is a zero-arg callable returning the awaitable to run, so
        the subsystem coroutine is only created when we actually run it.
        """
        try:
            await coro_factory()
        except Exception:
            # fail-open: advisory only — the poller runs every subsystem in one
            # loop, so one subsystem's failure must not kill the loop and starve
            # the others. We log/escalate below instead of propagating.
            count = self._subsystem_failures.get(name, 0) + 1
            self._subsystem_failures[name] = count
            log_ops_event(
                logger,
                logging.ERROR,
                "poller_subsystem_failed",
                f"{name} poll failed (consecutive failure #{count})",
                consecutive_failures=count,
                subsystem=name,
            )
            if count >= _POLLER_ESCALATE_AFTER:
                log_ops_event(
                    logger,
                    logging.CRITICAL,
                    "poller_subsystem_persistent_failure",
                    f"poller subsystem {name!r} has failed {count} times in a row"
                    " — it is persistently broken and needs attention.",
                    consecutive_failures=count,
                    subsystem=name,
                )
        else:
            self._subsystem_failures[name] = 0

    async def _poll_due_loop(self, session_factory: async_sessionmaker | None) -> None:
        from app.engine.arena import (
            ensure_auto_match,
            ensure_practice_arena,
            fill_and_start_auto_matches,
        )
        from app.engine.overdue_sweeper import sweep_overdue_turns
        from app.engine.seat_hold import sweep_held_seats

        factory = session_factory or SessionLocal

        async def _with_db(fn: Callable[[AsyncSession], Awaitable[None]]) -> None:
            async with factory() as db:
                await fn(db)

        while True:
            # 1st: fill overdue auto-matches with bots before start_due_games
            # evaluates player count — if reversed, auto-matches get cancelled.
            await self._run_subsystem(
                "fill_and_start_auto_matches",
                lambda: _with_db(fill_and_start_auto_matches),
            )

            # 2nd: recreate Practice Arena if the last one ended.
            await self._run_subsystem(
                "ensure_practice_arena", lambda: _with_db(ensure_practice_arena)
            )

            # 3rd: open the next 30-min auto-match window if none exists.
            await self._run_subsystem(
                "ensure_auto_match", lambda: _with_db(ensure_auto_match)
            )

            # 4th: confirm held seats whose AI came online, release expired ones —
            # BEFORE start_due_games counts players, so a seat that just went live
            # counts and one that timed out does not.
            await self._run_subsystem(
                "sweep_held_seats", lambda: sweep_held_seats(session_factory)
            )

            # 5th: existing logic — start/cancel non-arena games that are due.
            await self._run_subsystem(
                "start_due_games", lambda: self.start_due_games(session_factory)
            )

            # 6th: watchdog — self-heal without waiting for a server restart.
            #   a) Cancel ACTIVE games that have no players left (zombie games from
            #      destructive migrations that wiped the players table).  They can
            #      never make progress because _all_messaged / _all_submitted return
            #      False when active_player_count == 0.
            #   b) Restart ACTIVE games whose scheduler task has died (crashed tasks
            #      stay dead until the next server restart without this).
            await self._run_subsystem("watchdog", lambda: self._watchdog(factory))

            # 7th: overdue-turn sweeper — force-advance matches the watchdog's
            # restarts cannot heal (deterministic crashes re-crash on every
            # resume; a wedged-alive task never resolves its turn). Runs AFTER
            # the watchdog: a turn still frozen a full grace period past its
            # deadline has already survived ~30 restart attempts.
            await self._run_subsystem(
                "sweep_overdue_turns", lambda: sweep_overdue_turns(session_factory)
            )

            await asyncio.sleep(_START_POLL_SECONDS)

    async def _watchdog(self, factory: async_sessionmaker) -> None:
        """Cancel playerless ACTIVE games; restart ACTIVE games with dead tasks."""
        now = datetime.now(timezone.utc)
        async with factory() as db:
            active_games: list[Match] = list(
                (await db.execute(select(Match).where(Match.state == GameState.ACTIVE)))
                .scalars()
                .all()
            )
            for g in active_games:
                player_count = await active_player_count(
                    db, g.id, exclude_reserved=False
                )
                if player_count == 0:
                    mark_cancelled(g, now)
                    log_ops_event(
                        logger,
                        logging.WARNING,
                        "match_cancelled",
                        f"watchdog: cancelled game {g.id} — no active players",
                        match_id=g.id,
                        reason="no_active_players",
                    )
            await db.commit()

        # Restart tasks for games that are still ACTIVE but have no running task.
        async with factory() as db:
            still_active: list[str] = list(
                (
                    await db.execute(
                        select(Match.id).where(Match.state == GameState.ACTIVE)
                    )
                )
                .scalars()
                .all()
            )
        for match_id in still_active:
            if not self.is_running(match_id):
                logger.warning(
                    "watchdog: restarting dead task for game %s", match_id
                )
                self.start(match_id)

    async def resume_active_games_on_startup(
        self, session_factory: async_sessionmaker | None = None
    ) -> int:
        """On app startup, find any ACTIVE games and (re)start their loops."""
        factory = session_factory or SessionLocal
        async with factory() as db:
            games: list[Match] = list(
                (await db.execute(select(Match).where(Match.state == GameState.ACTIVE)))
                .scalars()
                .all()
            )
        for g in games:
            self.start(g.id)
        return len(games)


registry = SchedulerRegistry()


async def cancel_overdue_unfilled_games(db) -> int:
    """Cancel SCHEDULED/REGISTERING games that are past start with too few players.

    Read paths (the lobby) call this on render so a stuck game shows as cancelled
    even when the background poller hasn't swept it yet — the displayed state must
    not depend on a poller having run. Operates on the caller's session, so the
    same request sees the change, and returns how many games it cancelled.

    Cancel-only by design. Starting a due-and-full game spins up a turn-loop task;
    that side effect belongs to the poller, not a page render, so a full game still
    waiting to start is left untouched here. Only the (common) under-floor case —
    a game whose moment passed without enough players — is resolved on read.
    """
    now = datetime.now(timezone.utc)
    games = (
        (
            await db.execute(
                select(Match).where(
                    Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING])
                )
            )
        )
        .scalars()
        .all()
    )
    cancelled = 0
    for g in games:
        if ensure_aware(g.scheduled_start) > now:
            continue  # not due yet
        count = await _active_player_count(db, g.id)
        if count >= MIN_PLAYERS_TO_START:
            continue  # due and full — leave it for the poller to start
        mark_cancelled(g, now)
        cancelled += 1
        logger.info(
            "lobby-cancelled %s: %d players at start time (< %d)",
            g.id,
            count,
            MIN_PLAYERS_TO_START,
        )
    if cancelled:
        await db.commit()
    return cancelled


async def start_game(db, game: Match) -> None:
    """Transition SCHEDULED/REGISTERING → ACTIVE and kick off the loop."""
    from app.engine.seat_hold import release_held_seats

    # Any seat still held (join-before-connect) at start time never came online,
    # so drop it before the game runs — a held seat must never become a player
    # that defaults every turn.
    await release_held_seats(db, game.id)
    if game.state == GameState.SCHEDULED:
        # SCHEDULED can't jump straight to ACTIVE; open registration first so
        # start_due_games (which sweeps both states) doesn't throw on it.
        assert_transition(game.state, GameState.REGISTERING)
        game.state = GameState.REGISTERING
    assert_transition(game.state, GameState.ACTIVE)
    game.state = GameState.ACTIVE
    game.started_at = datetime.now(timezone.utc)
    await db.commit()
    registry.start(game.id)
