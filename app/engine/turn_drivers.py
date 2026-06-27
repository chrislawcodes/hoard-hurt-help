"""Per-game-shape turn drivers behind one interface.

The scheduler owns the game-agnostic skeleton (one asyncio task per match,
resume-on-restart, the due-game poller). How a *single turn advances* is a
`TurnDriver`:

- The **simultaneous, fixed-grid** driver for PD (`SimultaneousDriver`) lives in
  `app/engine/scheduler.py`, alongside the turn-loop helpers (`_open_turn`,
  `_wait_for_turn`, ...) that the rest of the engine and the test suite reference
  at that path.
- The **sequential, single-actor** driver for games like Liar's Dice lives here,
  isolated so that changes to it cannot reach PD's loop.

The scheduler selects a driver from the game module's
`config_defaults().simultaneous`.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select

from app.broadcast import publish
from app.engine.tokens import generate_turn_token
from app.engine.turn_clock import SUBMIT_POLL_SECONDS, now_utc
from app.engine.user_match_start import is_bot_kind
from app.models.player import Player
from app.models.turn import Turn

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.games.base import GameModule
    from app.models.match import Match


class TurnDriver(Protocol):
    """Drives one match's turn loop to completion (resume-aware via stored state)."""

    async def run_match(
        self, db: AsyncSession, game: Match, module: GameModule
    ) -> None: ...


class SequentialDriver:
    """Single-actor, variable-length turn loop (e.g. Liar's Dice).

    One player acts per turn, in an order the module decides via `next_actor`.
    A round (a "hand") ends when `next_actor` returns None; the match ends when
    `is_match_over` is True. The module owns all state via `match_state` /
    `player_state` and the round/match hooks — this driver is game-agnostic.

    Increment 1 scope: the loop orchestration + generic-state/hook wiring, proven
    by a sequential/hidden stub game. Obtaining a *live* move (waiting on a human
    agent's API submission, and smart bot decisions) is `_obtain_move`, which for
    now records the module's `default_move`; the live-wait + sequential-bot path
    wires into the scheduler in the next increment.
    """

    async def run_match(
        self, db: AsyncSession, game: Match, module: GameModule
    ) -> None:
        round_num = game.current_round or 1
        turn_num = game.current_turn or 0

        # Fresh start: open the first round (deal). Resume keeps the stored round.
        if not game.current_round:
            game.current_round = round_num
            await module.on_round_start(db, game, round_num)
            await db.commit()

        while True:
            actor = await module.next_actor(db, game)
            if actor is None:
                # The round (hand) is over — resolve it and advance or finish.
                await module.award_round(db, game, round_num)
                await publish(game.id, "round_ended", {"round": round_num})
                if await module.is_match_over(db, game):
                    break
                round_num += 1
                turn_num = 0
                game.current_round = round_num
                game.current_turn = 0
                await module.on_round_start(db, game, round_num)
                await db.commit()
                continue

            turn_num += 1
            turn = await self._open_actor_turn(db, game, round_num, turn_num)
            player = (
                await db.execute(
                    select(Player).where(
                        Player.match_id == game.id, Player.seat_name == actor
                    )
                )
            ).scalar_one()
            await publish(
                game.id,
                "turn_opened",
                {"round": round_num, "turn": turn_num, "phase": "act", "actor": actor},
            )
            await self._drive_actor_turn(db, game, turn, player, module)
            await module.resolve_turn(db, turn)
            await publish(
                game.id, "turn_resolved", {"round": round_num, "turn": turn_num}
            )

        await module.finalize(db, game)
        await publish(
            game.id, "game_completed", {"winner_player_id": game.winner_player_id}
        )

    async def _drive_actor_turn(
        self,
        db: AsyncSession,
        game: Match,
        turn: Turn,
        player: Player,
        module: GameModule,
    ) -> None:
        """Get the active player's move onto the turn.

        - **Bot** (scripted opponent): the platform submits on its behalf now.
          (A bot's *real* sequential decision is the game's own logic, wired in
          Phase C; until then it records the module's `default_move`.)
        - **Human agent**: the agent submits through the HTTP API (which calls
          `module.record_submission`), so the driver just waits for that write to
          appear. If the deadline passes with no submission, the driver records
          the module's `default_move` as the missed-turn move (flagged defaulted).
        """
        if await self._is_bot(db, player):
            move = await module.bot_move(db, game, player)
            await module.record_submission(db, turn, player, move, existing=None)
            return

        await self._wait_for_actor(db, turn, player)
        if not await self._has_real_submission(db, turn, player):
            move = await module.default_move(db, game, player)
            await module.record_submission(
                db, turn, player, move, existing=None, is_connector_fallback=True
            )

    async def _is_bot(self, db: AsyncSession, player: Player) -> bool:
        from app.models.agent import Agent

        agent = (
            await db.execute(select(Agent).where(Agent.id == player.agent_id))
        ).scalar_one_or_none()
        return agent is not None and is_bot_kind(agent.kind)

    async def _has_real_submission(
        self, db: AsyncSession, turn: Turn, player: Player
    ) -> bool:
        from app.models.turn import TurnSubmission

        sub = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == player.id,
                    TurnSubmission.was_defaulted.is_(False),
                )
            )
        ).scalar_one_or_none()
        return sub is not None

    async def _wait_for_actor(
        self, db: AsyncSession, turn: Turn, player: Player
    ) -> None:
        """Block until the active player submits (via the API) or the deadline passes."""
        deadline = turn.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        while True:
            remaining = (deadline - now_utc()).total_seconds()
            if remaining <= 0:
                return
            if await self._has_real_submission(db, turn, player):
                return
            await db.commit()  # fresh read next loop (see another connection's write)
            await asyncio.sleep(min(SUBMIT_POLL_SECONDS, remaining))

    async def _open_actor_turn(
        self, db: AsyncSession, game: Match, round_num: int, turn_num: int
    ) -> Turn:
        # Sequential turns are act-only (no talk phase): the message rides with
        # the move (Liar's Dice design D-5).
        #
        # Deliberately NOT unified with scheduler_turn_loop._open_turn (the
        # simultaneous opener): this one is a blind INSERT that writes only
        # `current_turn` (the SequentialDriver owns `current_round` in
        # run_match), while _open_turn is a get-or-create that writes both
        # pointers. Those are structural differences, not parameters — see the
        # C2 dedup note and tests/test_turn_openers.py.
        now = now_utc()
        turn = Turn(
            match_id=game.id,
            round=round_num,
            turn=turn_num,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=game.per_turn_deadline_seconds),
            phase="act",
        )
        db.add(turn)
        game.current_turn = turn_num
        await db.commit()
        await db.refresh(turn)
        return turn
