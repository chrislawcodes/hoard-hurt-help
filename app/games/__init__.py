"""Game module registry.

The platform resolves a `GameModule` by `game_type` via `get()`. Games register
themselves here on import; the platform never imports a game directly.
"""

from __future__ import annotations

from app.games.base import GameConfig, GameError, GameModule
from app.games.hoard_hurt_help.game import HoardHurtHelp

_REGISTRY: dict[str, GameModule] = {}


def register(module: GameModule) -> None:
    """Add a game module to the registry, keyed by its game_type."""
    _REGISTRY[module.game_type] = module


def get(game_type: str) -> GameModule:
    """Resolve a game module; raise GameError for an unregistered type."""
    module = _REGISTRY.get(game_type)
    if module is None:
        raise GameError(
            "UNKNOWN_GAME_TYPE", f"No game module registered for {game_type!r}."
        )
    return module


def known_types() -> list[str]:
    return sorted(_REGISTRY)


# Built-in games register themselves at import time.
register(HoardHurtHelp())

__all__ = ["GameConfig", "GameError", "GameModule", "get", "known_types", "register"]
