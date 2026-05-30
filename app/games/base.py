"""The Game plugin contract — the interface every turn-based game implements.

The platform (scheduler turn loop, agent API, viewer, lobby) depends only on
this `GameModule` protocol, never on a specific game. Games register themselves
in `app/games/__init__.py`, keyed by `game_type`. PD is the first module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.game import Game
    from app.models.player import Player
    from app.models.turn import Turn, TurnSubmission


class GameError(Exception):
    """Raised by a game module on an illegal move.

    `code` / `message` / `details` map straight onto the platform's standard
    error envelope, so a module owns its own validation errors while the
    platform stays game-agnostic.
    """

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class GameConfig:
    """Default settings a module ships with (a game may be created overriding them)."""

    total_rounds: int
    turns_per_round: int
    per_turn_deadline_seconds: int
    min_players: int
    max_players: int
    simultaneous: bool = True


class GameModule(Protocol):
    """The contract a turn-based game module implements."""

    game_type: str

    def config_defaults(self) -> GameConfig: ...

    def rules_text(self) -> str: ...

    def validate_move(
        self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]
    ) -> None:
        """Raise GameError if `move` is illegal for this game. Pure (no DB)."""
        ...

    async def record_submission(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
    ) -> None:
        """Persist a validated move into the module's storage (create or replace)."""
        ...

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None: ...

    async def award_round(self, db: AsyncSession, game: Game, round_num: int) -> None: ...

    async def finalize(self, db: AsyncSession, game: Game) -> None: ...

    def move_effect(self, action: str) -> tuple[int, int | None]:
        """Per-move display for the spectator viewer: (actor_delta, target_delta)."""
        ...
