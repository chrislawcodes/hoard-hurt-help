"""Preset packs for Sims."""

from __future__ import annotations

from dataclasses import dataclass

from .types import SimProfile


@dataclass(frozen=True)
class SimPackEntry:
    strategy: str
    truthfulness: int
    trust_model: str
    seed_offset: int


@dataclass(frozen=True)
class SimPack:
    id: str
    version: str
    name: str
    hidden: bool
    entries: list[SimPackEntry]


@dataclass(frozen=True)
class SimProfileChoice:
    id: str
    pack_id: str
    pack_name: str
    hidden: bool
    label: str
    description: str
    strategy: str
    truthfulness: int
    trust_model: str
    seed_offset: int


SIM_PACKS: dict[str, SimPack] = {
    "mixed_20": SimPack(
        id="mixed_20",
        version="v1",
        name="Mixed 20",
        hidden=False,
        entries=[
            SimPackEntry("coalition_seeker", 90, "even", 0),
            SimPackEntry("coalition_seeker", 80, "open", 1),
            SimPackEntry("loyal_partner", 80, "open", 2),
            SimPackEntry("loyal_partner", 65, "even", 3),
            SimPackEntry("grudger", 80, "bitter", 4),
            SimPackEntry("leader_pressure", 55, "careful", 5),
            SimPackEntry("opportunist", 35, "twitchy", 6),
            SimPackEntry("endgame_sniper", 65, "even", 7),
            SimPackEntry("diplomat", 80, "open", 8),
            SimPackEntry("crowd_follower", 45, "careful", 9),
        ],
    ),
    "coalition": SimPack(
        id="coalition",
        version="v1",
        name="Coalition",
        hidden=False,
        entries=[
            SimPackEntry("coalition_seeker", 90, "open", 0),
            SimPackEntry("loyal_partner", 80, "open", 1),
            SimPackEntry("diplomat", 80, "even", 2),
            SimPackEntry("coalition_seeker", 65, "careful", 3),
        ],
    ),
    "chaos": SimPack(
        id="chaos",
        version="v1",
        name="Chaos",
        hidden=False,
        entries=[
            SimPackEntry("grudger", 35, "bitter", 0),
            SimPackEntry("leader_pressure", 45, "twitchy", 1),
            SimPackEntry("opportunist", 25, "twitchy", 2),
            SimPackEntry("endgame_sniper", 35, "bitter", 3),
        ],
    ),
    "fixture_zero_floor": SimPack(
        id="fixture_zero_floor",
        version="v1",
        name="Fixture: Zero Floor",
        hidden=True,
        entries=[
            SimPackEntry("leader_pressure", 80, "even", 0),
            SimPackEntry("grudger", 80, "bitter", 1),
        ],
    ),
}


def resolve_pack(pack_id: str) -> SimPack:
    return SIM_PACKS[pack_id]


def pack_profile_choices(*, include_hidden: bool = False) -> list[SimProfileChoice]:
    choices: list[SimProfileChoice] = []
    for pack in SIM_PACKS.values():
        if pack.hidden and not include_hidden:
            continue
        for index, entry in enumerate(pack.entries):
            choices.append(
                SimProfileChoice(
                    id=f"{pack.id}:{index}",
                    pack_id=pack.id,
                    pack_name=pack.name,
                    hidden=pack.hidden,
                    label=_choice_label(entry),
                    description=_choice_description(entry, pack.version, index),
                    strategy=entry.strategy,
                    truthfulness=entry.truthfulness,
                    trust_model=entry.trust_model,
                    seed_offset=entry.seed_offset,
                )
            )
    return choices


def resolve_profile_choice(choice_id: str, *, seed_base: int = 0) -> SimProfile:
    pack_id, index_text = choice_id.split(":", 1)
    pack = resolve_pack(pack_id)
    index = int(index_text)
    entry = pack.entries[index]
    return SimProfile(
        strategy=entry.strategy,
        truthfulness=entry.truthfulness,
        trust_model=entry.trust_model,
        seed=seed_base + entry.seed_offset,
        version=pack.version,
        fixture_pack=pack.id if pack.hidden else None,
    )


def expand_pack(pack_id: str, *, seed_base: int = 0) -> list[SimProfile]:
    pack = resolve_pack(pack_id)
    return [
        SimProfile(
            strategy=entry.strategy,
            truthfulness=entry.truthfulness,
            trust_model=entry.trust_model,
            seed=seed_base + entry.seed_offset,
            version=pack.version,
            fixture_pack=pack.id if pack.hidden else None,
        )
        for entry in pack.entries
    ]


def _choice_label(entry: SimPackEntry) -> str:
    return (
        f"{entry.strategy.replace('_', ' ').title()} · "
        f"{entry.truthfulness}% · {entry.trust_model.title()}"
    )


def _choice_description(entry: SimPackEntry, pack_version: str, index: int) -> str:
    return f"Pack version {pack_version} · slot {index + 1}"
