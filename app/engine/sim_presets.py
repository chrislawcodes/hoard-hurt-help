"""Preset Sim profiles and helper naming utilities."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SimPreset:
    id: str
    name: str
    description: str
    strategy: str
    truthfulness: int
    trust_model: str
    seed_offset: int


SIM_PRESETS: tuple[SimPreset, ...] = (
    SimPreset(
        id="coalition_seeker",
        name="Coalition Seeker",
        description="Looks for useful mutual-help lanes and sticks to them.",
        strategy="coalition_seeker",
        truthfulness=90,
        trust_model="even",
        seed_offset=0,
    ),
    SimPreset(
        id="loyal_partner",
        name="Loyal Partner",
        description="Builds one reliable partnership and protects it.",
        strategy="loyal_partner",
        truthfulness=80,
        trust_model="open",
        seed_offset=1,
    ),
    SimPreset(
        id="grudger",
        name="Grudger",
        description="Starts open, then remembers betrayal hard.",
        strategy="grudger",
        truthfulness=80,
        trust_model="bitter",
        seed_offset=2,
    ),
    SimPreset(
        id="leader_pressure",
        name="Leader Pressure",
        description="Targets the current leader when the gap gets too large.",
        strategy="leader_pressure",
        truthfulness=55,
        trust_model="careful",
        seed_offset=3,
    ),
    SimPreset(
        id="opportunist",
        name="Opportunist",
        description="Helps when it helps, hoards when it can get away with it.",
        strategy="opportunist",
        truthfulness=35,
        trust_model="twitchy",
        seed_offset=4,
    ),
    SimPreset(
        id="endgame_sniper",
        name="Endgame Sniper",
        description="Plays patient early, then turns sharp near the finish.",
        strategy="endgame_sniper",
        truthfulness=65,
        trust_model="even",
        seed_offset=5,
    ),
    SimPreset(
        id="diplomat",
        name="Diplomat",
        description="Tries to keep peace and repair trust before conflict escalates.",
        strategy="diplomat",
        truthfulness=80,
        trust_model="open",
        seed_offset=6,
    ),
    SimPreset(
        id="crowd_follower",
        name="Crowd Follower",
        description="Copies the pattern that seems to be working.",
        strategy="crowd_follower",
        truthfulness=45,
        trust_model="careful",
        seed_offset=7,
    ),
)

GREEK_GODS: tuple[str, ...] = (
    "Zeus",
    "Hera",
    "Poseidon",
    "Demeter",
    "Athena",
    "Apollo",
    "Artemis",
    "Ares",
    "Aphrodite",
    "Hephaestus",
    "Hermes",
    "Dionysus",
    "Hades",
    "Persephone",
    "Hestia",
    "Cronus",
    "Rhea",
    "Nike",
    "Eros",
    "Helios",
)


def sim_presets() -> list[SimPreset]:
    return list(SIM_PRESETS)


def sim_preset_by_id(preset_id: str) -> SimPreset | None:
    return next((preset for preset in SIM_PRESETS if preset.id == preset_id), None)


def build_sim_bot_name(
    preset_name: str,
    *,
    used_names: set[str] | None = None,
    rng: random.Random | None = None,
) -> str:
    """Pick a friendly Greek-god name and pair it with the Sim profile name."""
    chooser = rng or random.SystemRandom()
    taken = used_names if used_names is not None else set()
    gods = list(GREEK_GODS)
    chooser.shuffle(gods)
    for god in gods:
        candidate = f"{god} - {preset_name}"
        if candidate not in taken:
            return candidate
    suffix = 2
    while True:
        candidate = f"{gods[0]} - {preset_name} {suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1
