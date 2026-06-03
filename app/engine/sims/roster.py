"""The Sim catalog the admin picks from when seating a game.

Static, presentation-facing data: the eight personalities (with a one-word
action *lean* for glanceable colour), the quick-add packs that bundle them, and
the Greek-god name pool used for default Sim names. The actual trait values for
each personality live in :mod:`app.engine.sim_presets`; this module only adds
the labels, descriptions, and grouping the admin screen needs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.engine.sim_presets import SIM_PRESETS

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
    for p in SIM_PRESETS
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


# Default Sim names. Single-token Greek gods so they pass the agent-id rule
# (letters/numbers/underscore). The admin can overwrite any of them.
SIM_NAME_POOL: tuple[str, ...] = (
    "Zeus", "Hera", "Poseidon", "Demeter", "Athena", "Apollo", "Artemis",
    "Ares", "Aphrodite", "Hephaestus", "Hermes", "Hestia", "Dionysus", "Hades",
    "Persephone", "Helios", "Selene", "Eos", "Nike", "Iris", "Atlas", "Nemesis",
    "Tyche", "Hypnos", "Hecate", "Eros", "Gaia", "Rhea", "Themis", "Hebe",
    "Triton", "Morpheus",
)


def is_known_personality(strategy: str) -> bool:
    return strategy in _PERSONALITY_IDS


def allocate_default_names(count: int, used: Iterable[str]) -> list[str]:
    """Pick ``count`` default names, skipping any already ``used``.

    Walks the Greek-god pool first, then falls back to ``Sim_N`` if a very large
    table exhausts it (the 20-player cap means that should never happen).
    """
    taken = set(used)
    out: list[str] = []
    for name in SIM_NAME_POOL:
        if len(out) >= count:
            break
        if name not in taken:
            out.append(name)
            taken.add(name)
    fallback = 1
    while len(out) < count:
        candidate = f"Sim_{fallback}"
        fallback += 1
        if candidate not in taken:
            out.append(candidate)
            taken.add(candidate)
    return out
