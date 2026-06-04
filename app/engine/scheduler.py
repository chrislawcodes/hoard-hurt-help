"""Per-game asyncio scheduler that drives the turn loop.

For each ACTIVE game, a `_run_game` task runs:
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
"""

import asyncio
import functools
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.broadcast import publish
from app.db import SessionLocal
from app.engine import resolver
from app.engine.sims.service import auto_submit_sim_phase
from app.engine.state_machine import assert_transition
from app.engine.tokens import generate_turn_token
from app.games import get as get_game_module
from app.games.base import GameError
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission

logger = logging.getLogger(__name__)

# How often the loop checks whether every active player has submitted (so it can
# resolve a turn early instead of waiting out the whole deadline).
_SUBMIT_POLL_SECONDS = 0.25
# How often the background poller checks for games that are due to start.
_START_POLL_SECONDS = 2.0
# Hard floor of players to actually run a game. `min_players` on a game is a
# SOFT lobby target (what the admin advertises); this is the rules-mechanical
# minimum. A due game with fewer than this is cancelled rather than left stuck.
MIN_PLAYERS_TO_START = 3


def _as_aware(dt: datetime) -> datetime:
    """SQLite drops tz info on read; normalize to UTC-aware for comparisons."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _active_player_count(db, match_id: str) -> int:
    """Seats currently held in a game — a player who left frees their seat."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(Player)
            .where(Player.match_id == match_id, Player.left_at.is_(None))
        )
    ) or 0


class SchedulerRegistry:
    """Singleton-ish registry of running per-game tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._poller: asyncio.Task | None = None

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
                if _as_aware(g.scheduled_start) > now:
                    continue  # not due yet
                count = await _active_player_count(db, g.id)
                if count >= MIN_PLAYERS_TO_START:
                    await start_game(db, g)
                    started += 1
                    logger.info("auto-started %s with %d players", g.id, count)
                else:
                    g.state = GameState.CANCELLED
                    g.cancelled_at = now
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

    async def _poll_due_loop(self, session_factory: async_sessionmaker | None) -> None:
        from app.engine.arena import (
            ensure_auto_match,
            ensure_practice_arena,
            fill_and_start_auto_matches,
        )

        factory = session_factory or SessionLocal
        while True:
            # 1st: fill overdue auto-matches with Sims before start_due_games
            # evaluates player count — if reversed, auto-matches get cancelled.
            try:
                async with factory() as db:
                    await fill_and_start_auto_matches(db)
            except Exception:
                logger.exception("fill_and_start_auto_matches poll failed")

            # 2nd: recreate Practice Arena if the last one ended.
            try:
                async with factory() as db:
                    await ensure_practice_arena(db)
            except Exception:
                logger.exception("ensure_practice_arena poll failed")

            # 3rd: open the next 30-min auto-match window if none exists.
            try:
                async with factory() as db:
                    await ensure_auto_match(db)
            except Exception:
                logger.exception("ensure_auto_match poll failed")

            # 4th: existing logic — start/cancel non-arena games that are due.
            try:
                await self.start_due_games(session_factory)
            except Exception:  # never let the poller die on a transient error
                logger.exception("start_due_games poll failed")
            await asyncio.sleep(_START_POLL_SECONDS)

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


async def _run_game(match_id: str) -> None:
    """The actual loop for one game."""
    async with SessionLocal() as db:
        game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()

        if game.state != GameState.ACTIVE:
            return

        # The platform drives the loop through the game's module — never a
        # hard-coded resolver. Skip (don't crash the poller) on an unknown type.
        try:
            module = get_game_module(game.game)
        except GameError:
            logger.error(
                "Match %s has unknown game_type %r — skipping its turn loop.",
                game.id,
                game.game,
            )
            return

        # Resume from current_round/current_turn — supports mid-game restart.
        start_round = game.current_round if game.current_round else 1
        start_turn = game.current_turn if game.current_turn else 1

        for round_num in range(start_round, game.total_rounds + 1):
            if round_num != start_round or start_turn == 1:
                # Reset round scores at start of each fresh round.
                players: list[Player] = list(
                    (await db.execute(select(Player).where(Player.match_id == game.id)))
                    .scalars()
                    .all()
                )
                for p in players:
                    p.current_round_score = 0
                await db.commit()

            # If resuming mid-round, continue from start_turn; else start at 1.
            first_turn = start_turn if round_num == start_round else 1

            for turn_num in range(first_turn, game.turns_per_round + 1):
                turn = await _open_turn(db, game, round_num, turn_num)
                if turn.resolved_at is not None:
                    continue
                # --- TALK phase (skip if already talk-resolved on resume) ---
                if turn.talk_resolved_at is None:
                    await publish(
                        game.id,
                        "turn_opened",
                        {
                            "round": round_num,
                            "turn": turn_num,
                            "phase": "talk",
                            "deadline": turn.deadline_at.isoformat(),
                        },
                    )
                    await auto_submit_sim_phase(db, game, turn, module, phase="talk")
                    await _wait_for_messages(db, turn)
                    await resolver.finalize_talk_phase(db, turn)
                    await _begin_act_phase(db, game, turn)
                    await publish(game.id, "turn_talked", {"round": round_num, "turn": turn_num})
                elif turn.phase != "act":
                    await _begin_act_phase(db, game, turn)
                # --- ACT phase ---
                await publish(
                    game.id,
                    "turn_opened",
                    {
                        "round": round_num,
                        "turn": turn_num,
                        "phase": "act",
                        "deadline": turn.deadline_at.isoformat(),
                    },
                )
                await auto_submit_sim_phase(db, game, turn, module, phase="act")
                await _wait_for_turn(db, turn)
                await module.resolve_turn(db, turn)
                await publish(
                    game.id,
                    "turn_resolved",
                    {"round": round_num, "turn": turn_num},
                )
            await module.award_round(db, game, round_num)
            await publish(game.id, "round_ended", {"round": round_num})

        await module.finalize(db, game)
        await publish(game.id, "game_completed", {"winner_player_id": game.winner_player_id})


async def _run_game_guarded(match_id: str) -> None:
    """Run one game loop and log any crash before re-raising it."""
    try:
        await _run_game(match_id)
    except Exception as exc:
        logger.error("game %s loop task crashed", match_id, exc_info=exc)
        raise


async def _open_turn(db, game: Match, round_num: int, turn_num: int) -> Turn:
    """Open the turn row for (game, round, turn), reusing it if it already exists.

    On a mid-game restart the loop resumes from game.current_round/current_turn,
    which points at a turn that was already opened before the crash. A blind
    INSERT would hit uq_turns_game_id_round_turn and kill the whole game loop, so
    we get-or-create: an existing row is handed back unchanged and the caller
    decides (via resolved_at) whether it still needs resolving.
    """
    existing = (
        await db.execute(
            select(Turn).where(
                Turn.match_id == game.id,
                Turn.round == round_num,
                Turn.turn == turn_num,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        game.current_round = round_num
        game.current_turn = turn_num
        await db.commit()
        return existing

    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id=game.id,
        round=round_num,
        turn=turn_num,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=game.per_turn_deadline_seconds),
        phase="talk",
    )
    db.add(turn)
    game.current_round = round_num
    game.current_turn = turn_num
    await db.commit()
    await db.refresh(turn)
    return turn


async def _all_submitted(db, turn: Turn) -> bool:
    """True once every active (non-left) player has a real submission this turn.

    Commits first so the read starts a fresh transaction and sees rows the
    submit endpoint committed on its own connection (rather than a stale snapshot).
    """
    await db.commit()
    active = await db.scalar(
        select(func.count())
        .select_from(Player)
        .where(Player.match_id == turn.match_id, Player.left_at.is_(None))
    )
    submitted = await db.scalar(
        select(func.count())
        .select_from(TurnSubmission)
        .where(TurnSubmission.turn_id == turn.id, TurnSubmission.was_defaulted.is_(False))
    )
    return bool(active) and (submitted or 0) >= active


async def _all_messaged(db, turn: Turn) -> bool:
    """True once every active (non-left) player has a real talk message this turn."""
    await db.commit()
    active = await db.scalar(
        select(func.count())
        .select_from(Player)
        .where(Player.match_id == turn.match_id, Player.left_at.is_(None))
    )
    messaged = await db.scalar(
        select(func.count())
        .select_from(TurnMessage)
        .where(TurnMessage.turn_id == turn.id, TurnMessage.was_defaulted.is_(False))
    )
    return bool(active) and (messaged or 0) >= active


async def _wait_for_messages(db, turn: Turn) -> None:
    """Block until the talk deadline, or until all active players have messaged."""
    deadline = _as_aware(turn.deadline_at)
    while True:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        if await _all_messaged(db, turn):
            return
        await asyncio.sleep(min(_SUBMIT_POLL_SECONDS, remaining))


async def _begin_act_phase(db, game: Match, turn: Turn) -> None:
    """Transition a turn from talk to act and reset the turn token/deadline."""
    turn.phase = "act"
    turn.turn_token = generate_turn_token()
    turn.deadline_at = datetime.now(timezone.utc) + timedelta(
        seconds=game.per_turn_deadline_seconds
    )
    await db.commit()


async def _wait_for_turn(db, turn: Turn) -> None:
    """Block until the turn deadline, or until all active players have submitted."""
    deadline = _as_aware(turn.deadline_at)
    while True:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        if await _all_submitted(db, turn):
            return
        await asyncio.sleep(min(_SUBMIT_POLL_SECONDS, remaining))


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
        if _as_aware(g.scheduled_start) > now:
            continue  # not due yet
        count = await _active_player_count(db, g.id)
        if count >= MIN_PLAYERS_TO_START:
            continue  # due and full — leave it for the poller to start
        g.state = GameState.CANCELLED
        g.cancelled_at = now
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
