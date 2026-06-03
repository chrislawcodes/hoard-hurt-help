"""The Match plugin contract — the interface every turn-based game implements.

The platform (scheduler turn loop, agent API, viewer, lobby) depends only on
this `GameModule` protocol, never on a specific game. Games register themselves
in `app/games/__init__.py`, keyed by `game_type`. PD is the first module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.match import Match
    from app.models.player import Player
    from app.models.turn import Turn, TurnMessage, TurnSubmission


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


@dataclass(frozen=True)
class StrategyPreset:
    """A named starting strategy a game offers (a player picks one or writes their own)."""

    id: str
    name: str
    description: str
    prompt: str


@dataclass(frozen=True)
class GameTheme:
    """A game's color identity, layered *inside* the fixed platform shell.

    The platform chrome — nav, footer, brand, button shape, type scale — is the
    shared design language across every game and never reads these values. A
    game's pages stamp `data-game=<key>` on the content region (`<main>`) and
    the platform applies `vars` as scoped CSS custom properties there, so only
    that game's content takes the tint while the surrounding chrome stays
    constant. The color travels with the module: adding a game means returning a
    theme here, touching no shared CSS or template.

    `key` is the `data-game` value (use the module's `game_type`). `vars` maps
    CSS custom-property names to values (e.g. `{"--brand": "#e2640e"}`) — only
    content tokens (accents, semantic move colors, surfaces), never chrome.
    """

    key: str
    vars: dict[str, str]


class GameModule(Protocol):
    """The contract a turn-based game module implements."""

    game_type: str

    def config_defaults(self) -> GameConfig: ...

    def rules_text(self) -> str: ...

    def strategy_presets(self) -> list[StrategyPreset]:
        """Named starting strategies offered to a player entering this game."""
        ...

    def default_strategy(self) -> str:
        """Strategy text a player's entry textarea is pre-filled with."""
        ...

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

    async def record_message(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        message: str,
        thinking: str,
        *,
        existing: TurnMessage | None,
    ) -> None:
        """Persist a validated talk-phase message into the module's storage."""
        ...

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None: ...

    async def award_round(self, db: AsyncSession, game: Match, round_num: int) -> None: ...

    async def finalize(self, db: AsyncSession, game: Match) -> None: ...

    def move_effect(self, action: str) -> tuple[int, int | None]:
        """Per-move display for the spectator viewer: (actor_delta, target_delta)."""
        ...

    def theme(self) -> GameTheme:
        """This game's content color identity (see `GameTheme`)."""
        ...
