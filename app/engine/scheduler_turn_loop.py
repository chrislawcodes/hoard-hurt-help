"""Per-match turn-loop execution, split out of `app/engine/scheduler.py`.

`scheduler.py` owns the registry and the due-game start poller. This module
owns the freeze-prone part: running a single match's turn loop to completion.
A past production freeze came from turn resolution on resume, so the resume
idempotency here (`_open_turn`'s get-or-create) is preserved exactly — this is
a pure relocation, behavior-identical to when these lived in `scheduler.py`.

`scheduler.SchedulerRegistry.start()` imports `_run_game_guarded` from here, so
the dependency is one-directional: scheduler -> scheduler_turn_loop. At runtime
this code reads its *test-patchable* collaborators — `SessionLocal`, `logger`,
`publish`, `auto_submit_bot_phase`, `record_background_incident`, the `_run_game`
entry point, and the `_wait_for_*` helpers — through the `scheduler` module
(re-exported there), not through local bindings. That is exactly the patch
surface these functions had before the split: the suite patches
`scheduler.publish`, `scheduler.SessionLocal`, etc. and expects the turn loop to
honor it. The `from app.engine import scheduler` is done lazily inside the
function bodies so the import cycle never closes at module load.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.engine import resolver
from app.engine.tokens import generate_turn_token
from app.engine.turn_drivers import SequentialDriver, TurnDriver
from app.games import get as get_game_module
from app.games.base import GameError
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.ops_events import log_ops_event

if TYPE_CHECKING:
    from app.games.base import GameModule

# How often the loop checks whether every active player has submitted (so it can
# resolve a turn early instead of waiting out the whole deadline).
_SUBMIT_POLL_SECONDS = 0.25


class SimultaneousDriver:
    """PD's turn loop: every active player acts each turn over a fixed rounds×turns
    grid, talk→act per turn, resolving all submissions at once.

    Kept alongside the turn-loop helpers below (`_open_turn`, `_wait_for_turn`,
    ...) because it is inseparable from them. The isolated sequential driver lives
    in `turn_drivers.py`. Behavior is unchanged from when this was the body of
    `_run_game`.
    """

    async def run_match(
        self, db: AsyncSession, game: Match, module: GameModule
    ) -> None:
        # Resume from current_round/current_turn — supports mid-game restart. The
        # resume decisions (which round resets scores, where the first round picks
        # up) are computed here and passed into `_run_round` so the round/turn
        # bodies stay plain loops with no resume branching of their own.
        start_round = game.current_round if game.current_round else 1
        start_turn = game.current_turn if game.current_turn else 1

        for round_num in range(start_round, game.total_rounds + 1):
            # Reset round scores at the start of every fresh round, but NOT when
            # resuming mid-round (round == start_round and start_turn != 1).
            reset_scores = round_num != start_round or start_turn == 1
            # If resuming mid-round, continue from start_turn; else start at 1.
            first_turn = start_turn if round_num == start_round else 1
            await _run_round(
                db,
                game,
                module,
                round_num,
                first_turn=first_turn,
                reset_scores=reset_scores,
            )

        # publish is read off `scheduler` so the suite's `scheduler.publish` patch
        # reaches it; lazy import avoids closing the scheduler<->turn_loop cycle.
        from app.engine import scheduler

        await module.finalize(db, game)
        await scheduler.publish(game.id, "game_completed", {"winner_player_id": game.winner_player_id})


async def _run_round(
    db: AsyncSession,
    game: Match,
    module: GameModule,
    round_num: int,
    *,
    first_turn: int,
    reset_scores: bool,
) -> None:
    """Run one round: optional score reset, each turn in order, then award it.

    `reset_scores` / `first_turn` carry `run_match`'s resume decision so mid-game
    restart behaves identically — a round resumed partway through neither re-zeroes
    scores nor replays turns it already finished.
    """
    from app.engine import scheduler

    if reset_scores:
        players: list[Player] = list(
            (await db.execute(select(Player).where(Player.match_id == game.id)))
            .scalars()
            .all()
        )
        for p in players:
            p.current_round_score = 0
        await db.commit()

    for turn_num in range(first_turn, game.turns_per_round + 1):
        await _run_turn(db, game, module, round_num, turn_num)

    await module.award_round(db, game, round_num)
    await scheduler.publish(game.id, "round_ended", {"round": round_num})


async def _run_turn(
    db: AsyncSession,
    game: Match,
    module: GameModule,
    round_num: int,
    turn_num: int,
) -> None:
    """Run one turn's talk→act lifecycle. Idempotent on resume: an already-resolved
    turn returns immediately, and the talk phase is skipped if already talk-resolved.

    `publish` / `auto_submit_bot_phase` / the `_wait_for_*` helpers are read off
    `scheduler` so the suite's `scheduler.publish` (etc.) patches reach this loop —
    the same patch surface as before the split. The lazy import keeps the
    scheduler<->turn_loop cycle from closing at module load.
    """
    from app.engine import scheduler

    turn = await _open_turn(db, game, round_num, turn_num)
    if turn.resolved_at is not None:
        return
    # --- TALK phase (skip if already talk-resolved on resume) ---
    if turn.talk_resolved_at is None:
        await scheduler.publish(
            game.id,
            "turn_opened",
            {
                "round": round_num,
                "turn": turn_num,
                "phase": "talk",
                "deadline": turn.deadline_at.isoformat(),
            },
        )
        await scheduler.auto_submit_bot_phase(db, game, turn, module, phase="talk")
        await scheduler._wait_for_messages(db, turn)
        await resolver.finalize_talk_phase(db, turn)
        await _begin_act_phase(db, game, turn)
        await scheduler.publish(game.id, "turn_talked", {"round": round_num, "turn": turn_num})
    elif turn.phase != "act":
        await _begin_act_phase(db, game, turn)
    # --- ACT phase ---
    await scheduler.publish(
        game.id,
        "turn_opened",
        {
            "round": round_num,
            "turn": turn_num,
            "phase": "act",
            "deadline": turn.deadline_at.isoformat(),
        },
    )
    await scheduler.auto_submit_bot_phase(db, game, turn, module, phase="act")
    await scheduler._wait_for_turn(db, turn)
    await module.resolve_turn(db, turn)
    await scheduler.publish(
        game.id,
        "turn_resolved",
        {"round": round_num, "turn": turn_num},
    )


def _select_driver(module: GameModule) -> TurnDriver:
    """Pick the loop shape from the game's config — the previously-unused
    GameConfig.simultaneous flag. PD is simultaneous; sequential games (Liar's
    Dice) get the isolated SequentialDriver."""
    if module.config_defaults().simultaneous:
        return SimultaneousDriver()
    return SequentialDriver()


async def _run_game(match_id: str) -> None:
    """Resolve the match's game module, pick its turn driver, and run the match."""
    from app.engine import scheduler

    async with scheduler.SessionLocal() as db:
        game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()

        if game.state != GameState.ACTIVE:
            return

        # The platform drives the loop through the game's module — never a
        # hard-coded resolver. Creation-time validation should already reject
        # unknown types; this is defense in depth. If one still reaches here we
        # must NOT leave the match ACTIVE forever (the zombie state) — cancel it
        # loudly so it shows as terminal and stops being polled.
        try:
            module = get_game_module(game.game)
        except GameError:
            game.state = GameState.CANCELLED
            game.cancelled_at = datetime.now(timezone.utc)
            await db.commit()
            log_ops_event(
                scheduler.logger,
                logging.ERROR,
                "match_cancelled",
                f"Match {game.id} has unknown game_type {game.game!r} — cancelled"
                " (cannot run its turn loop). This should have been rejected at"
                " creation time.",
                game_type=game.game,
                match_id=game.id,
                reason="unknown_game_type",
            )
            return

        await _select_driver(module).run_match(db, game, module)


async def _run_game_guarded(match_id: str) -> None:
    """Run one game loop and record any crash before re-raising it.

    This is the single chokepoint for the fire-and-forget turn loop. A crash
    here used to surface only as a log line (and the match silently froze), so
    we now also persist a queryable incident keyed by match_id plus the
    round/turn it died on, and emit a greppable ops-event line.
    """
    from app.engine import scheduler

    try:
        await scheduler._run_game(match_id)
    except Exception as exc:
        round_num: int | None = None
        turn_num: int | None = None
        try:
            async with scheduler.SessionLocal() as db:
                match = (
                    await db.execute(select(Match).where(Match.id == match_id))
                ).scalar_one_or_none()
                if match is not None:
                    round_num = match.current_round
                    turn_num = match.current_turn
        except Exception:  # never let crash-reporting hide the original crash
            scheduler.logger.exception(
                "could not read match position for crash context match_id=%s",
                match_id,
            )
        log_ops_event(
            scheduler.logger,
            logging.ERROR,
            "turn_loop_crashed",
            f"Turn loop for match {match_id} crashed at R{round_num}T{turn_num}",
            match_id=match_id,
            round=round_num,
            turn=turn_num,
            error_type=type(exc).__name__,
        )
        await scheduler.record_background_incident(
            source="scheduler:_run_game",
            exc=exc,
            match_id=match_id,
            stage="turn_loop",
            context={"round": round_num, "turn": turn_num},
        )
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
    deadline = ensure_aware(turn.deadline_at)
    while True:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        if await _all_messaged(db, turn):
            return
        await asyncio.sleep(min(_SUBMIT_POLL_SECONDS, remaining))


async def _begin_act_phase(db, game: Match, turn: Turn) -> None:
    """Transition a turn from talk to act: switch the phase and reset the deadline.

    The turn_token is deliberately left UNCHANGED across the talk->act handoff. It
    used to be re-minted here, which silently broke a slow player: if their talk
    message landed a moment after the talk window closed, it arrived holding the
    now-defunct token and was rejected outright (STALE_TURN_TOKEN), so the talk was
    dropped and the player fell through to act-only. This bit hardest on the first
    turn of a round, when an agent deliberates longest. Keeping one stable token
    per turn means a late talk is recognized as "the talk window already closed"
    (handled gracefully in submit_talk) rather than a hard error, and the player
    can act with the token it already holds. The `phase` column — not the token —
    is what tells talk and act apart.
    """
    turn.phase = "act"
    turn.deadline_at = datetime.now(timezone.utc) + timedelta(
        seconds=game.per_turn_deadline_seconds
    )
    await db.commit()


async def _wait_for_turn(db, turn: Turn) -> None:
    """Block until the turn deadline, or until all active players have submitted."""
    deadline = ensure_aware(turn.deadline_at)
    while True:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        if await _all_submitted(db, turn):
            return
        await asyncio.sleep(min(_SUBMIT_POLL_SECONDS, remaining))
