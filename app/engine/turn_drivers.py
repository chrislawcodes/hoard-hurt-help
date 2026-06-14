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

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from app.broadcast import publish
from app.engine.tokens import generate_turn_token
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
            move = await self._obtain_move(db, game, turn, player, module)
            await module.record_submission(db, turn, player, move, existing=None)
            await module.resolve_turn(db, turn)
            await publish(
                game.id, "turn_resolved", {"round": round_num, "turn": turn_num}
            )

        await module.finalize(db, game)
        await publish(
            game.id, "game_completed", {"winner_player_id": game.winner_player_id}
        )

    async def _obtain_move(
        self,
        db: AsyncSession,
        game: Match,
        turn: Turn,
        player: Player,
        module: GameModule,
    ) -> dict[str, Any]:
        # Increment 1: record the module's default move (a placeholder for the
        # missed-turn / bot path). The live human-API wait and sequential-bot
        # decision wire in with the scheduler in the next increment.
        return await module.default_move(db, game, player)

    async def _open_actor_turn(
        self, db: AsyncSession, game: Match, round_num: int, turn_num: int
    ) -> Turn:
        # Sequential turns are act-only (no talk phase): the message rides with
        # the move (Liar's Dice design D-5).
        now = _now()
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
