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
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.broadcast import publish
from app.db import SessionLocal
from app.engine.resolver import award_round_winners, finalize_game, resolve_turn
from app.engine.state_machine import assert_transition
from app.engine.tokens import generate_turn_token
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.turn import Turn


class SchedulerRegistry:
    """Singleton-ish registry of running per-game tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def is_running(self, game_id: str) -> bool:
        t = self._tasks.get(game_id)
        return t is not None and not t.done()

    def start(self, game_id: str) -> None:
        if self.is_running(game_id):
            return
        self._tasks[game_id] = asyncio.create_task(_run_game(game_id))

    def stop(self, game_id: str) -> None:
        t = self._tasks.pop(game_id, None)
        if t and not t.done():
            t.cancel()

    async def resume_active_games_on_startup(
        self, session_factory: async_sessionmaker | None = None
    ) -> int:
        """On app startup, find any ACTIVE games and (re)start their loops."""
        factory = session_factory or SessionLocal
        async with factory() as db:
            games: list[Game] = (
                (await db.execute(select(Game).where(Game.state == GameState.ACTIVE)))
                .scalars()
                .all()
            )
        for g in games:
            self.start(g.id)
        return len(games)


registry = SchedulerRegistry()


async def _run_game(game_id: str) -> None:
    """The actual loop for one game."""
    async with SessionLocal() as db:
        game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()

        if game.state != GameState.ACTIVE:
            return

        # Resume from current_round/current_turn — supports mid-game restart.
        start_round = game.current_round if game.current_round else 1
        start_turn = game.current_turn if game.current_turn else 1

        for round_num in range(start_round, game.total_rounds + 1):
            if round_num != start_round or start_turn == 1:
                # Reset round scores at start of each fresh round.
                players: list[Player] = (
                    (await db.execute(select(Player).where(Player.game_id == game.id)))
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
                await publish(
                    game.id,
                    "turn_opened",
                    {"round": round_num, "turn": turn_num, "deadline": turn.deadline_at.isoformat()},
                )

                await _sleep_until(turn.deadline_at)

                await resolve_turn(db, turn)
                await publish(
                    game.id,
                    "turn_resolved",
                    {"round": round_num, "turn": turn_num},
                )

            await award_round_winners(db, game, round_num)
            await publish(game.id, "round_ended", {"round": round_num})

        await finalize_game(db, game)
        await publish(game.id, "game_completed", {"winner_player_id": game.winner_player_id})


async def _open_turn(db, game: Game, round_num: int, turn_num: int) -> Turn:
    now = datetime.now(timezone.utc)
    from datetime import timedelta

    turn = Turn(
        game_id=game.id,
        round=round_num,
        turn=turn_num,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=game.per_turn_deadline_seconds),
    )
    db.add(turn)
    game.current_round = round_num
    game.current_turn = turn_num
    await db.commit()
    await db.refresh(turn)
    return turn


async def _sleep_until(when: datetime) -> None:
    delta = (when - datetime.now(timezone.utc)).total_seconds()
    if delta > 0:
        await asyncio.sleep(delta)


async def start_game(db, game: Game) -> None:
    """Transition SCHEDULED/REGISTERING → ACTIVE and kick off the loop."""
    assert_transition(game.state, GameState.ACTIVE)
    game.state = GameState.ACTIVE
    game.started_at = datetime.now(timezone.utc)
    await db.commit()
    registry.start(game.id)
