"""The Sim catalog the admin picks from when seating a game.

Static, presentation-facing data: the eight personalities (with a one-word
action *lean* for glanceable colour), the quick-add packs that bundle them, and
the historical leader name pool used for default Sim names. The actual trait
values for each personality live in :mod:`app.engine.sim_presets`; this module
only adds the labels, descriptions, and grouping the admin screen needs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.engine.bot_presets import (
    HISTORICAL_BOT_NAME_POOL,
    BOT_PRESETS,
    allocate_default_bot_names,
)

# Each personality leans toward one of the three actions. Used only to colour a
# dot in the picker so the admin can read cooperative / aggressive / self at a
# glance — it does not change how the Sim plays.
_LEAN: dict[str, str] = {
    "coalition_seeker": "help",
    "loyal_partner": "help",
    "diplomat": "help",
    "grudger": "hurt",
    "leader_pressure": "hurt",
    "endgame_sniper": "hurt",
    "opportunist": "hoard",
    "crowd_follower": "hoard",
}


@dataclass(frozen=True)
class Personality:
    id: str
    label: str
    description: str
    lean: str  # "help" | "hurt" | "hoard"


PERSONALITIES: tuple[Personality, ...] = tuple(
    Personality(p.id, p.name, p.description, _LEAN.get(p.id, "hoard"))
    for p in BOT_PRESETS
)

_PERSONALITY_IDS: frozenset[str] = frozenset(p.id for p in PERSONALITIES)


@dataclass(frozen=True)
class Pack:
    id: str
    label: str
    description: str
    strategies: tuple[str, ...]


# Quick-add bundles. Each is just a list of personalities; "Balanced" is one of
# each, the other two skew the table cooperative or aggressive.
PACKS: tuple[Pack, ...] = (
    Pack(
        "balanced",
        "Balanced",
        "One of each personality.",
        tuple(p.id for p in PERSONALITIES),
    ),
    Pack(
        "cooperative",
        "Cooperative",
        "Alliance-leaning table.",
        ("coalition_seeker", "loyal_partner", "diplomat", "coalition_seeker"),
    ),
    Pack(
        "cutthroat",
        "Cutthroat",
        "Aggressive, volatile table.",
        ("grudger", "leader_pressure", "opportunist", "endgame_sniper"),
    ),
)


# Default Sim names. These are historical generals and leaders; multi-word
# names use spaces for display.
SIM_NAME_POOL: tuple[str, ...] = HISTORICAL_BOT_NAME_POOL


def is_known_personality(strategy: str) -> bool:
    return strategy in _PERSONALITY_IDS


def allocate_default_names(count: int, used: Iterable[str]) -> list[str]:
    """Pick ``count`` default names, skipping any already ``used``.

    Walks the historical leader pool first, then falls back to ``Leader N`` if
    a very large table exhausts it.
    """
    return allocate_default_bot_names(count, used_names=set(used))
