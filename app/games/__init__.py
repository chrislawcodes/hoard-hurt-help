"""Match module registry.

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


def unregister(game_type: str) -> None:
    """Remove a game module from the registry; no-op if it is not registered.

    Used by tests to tear down stub modules so they do not leak into later
    tests. Built-in games stay registered for the life of the process.
    """
    _REGISTRY.pop(game_type, None)


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


def is_admin_only(game_type: str) -> bool:
    """True if this game is hidden from non-admins (under construction).

    Unknown types are treated as not admin-only (callers that care about
    existence validate it separately); a known game reports its config flag.
    """
    module = _REGISTRY.get(game_type)
    return module is not None and module.config_defaults().admin_only


def visible_types(*, include_admin_only: bool) -> list[str]:
    """Registered game types, optionally excluding admin-only (hidden) games."""
    if include_admin_only:
        return known_types()
    return sorted(t for t in _REGISTRY if not is_admin_only(t))


# Built-in games register themselves at import time.
register(HoardHurtHelp())

__all__ = [
    "GameConfig",
    "GameError",
    "GameModule",
    "get",
    "is_admin_only",
    "known_types",
    "register",
    "unregister",
    "visible_types",
]
