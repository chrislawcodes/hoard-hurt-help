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

from typing import TYPE_CHECKING, Protocol

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

    Lands in Phase B (validated by a sequential/hidden stub game). Until then no
    sequential game is registered, so the scheduler never selects this driver.
    """

    async def run_match(
        self, db: AsyncSession, game: Match, module: GameModule
    ) -> None:
        raise NotImplementedError(
            "SequentialDriver is implemented in Phase B; no sequential game is "
            "registered yet."
        )
