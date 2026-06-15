"""Preset packs for bots."""

from __future__ import annotations

from dataclasses import dataclass

from .types import BotProfile


@dataclass(frozen=True)
class BotPackEntry:
    strategy: str
    truthfulness: int
    trust_model: str
    seed_offset: int


@dataclass(frozen=True)
class BotPack:
    id: str
    version: str
    name: str
    hidden: bool
    entries: list[BotPackEntry]


@dataclass(frozen=True)
class BotProfileChoice:
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


BOT_PACKS: dict[str, BotPack] = {
    "mixed_20": BotPack(
        id="mixed_20",
        version="v1",
        name="Mixed 20",
        hidden=False,
        entries=[
            BotPackEntry("coalition_seeker", 90, "even", 0),
            BotPackEntry("coalition_seeker", 80, "open", 1),
            BotPackEntry("loyal_partner", 80, "open", 2),
            BotPackEntry("loyal_partner", 65, "even", 3),
            BotPackEntry("grudger", 80, "bitter", 4),
            BotPackEntry("leader_pressure", 55, "careful", 5),
            BotPackEntry("opportunist", 35, "twitchy", 6),
            BotPackEntry("endgame_sniper", 65, "even", 7),
            BotPackEntry("diplomat", 80, "open", 8),
            BotPackEntry("crowd_follower", 45, "careful", 9),
        ],
    ),
    "coalition": BotPack(
        id="coalition",
        version="v1",
        name="Coalition",
        hidden=False,
        entries=[
            BotPackEntry("coalition_seeker", 90, "open", 0),
            BotPackEntry("loyal_partner", 80, "open", 1),
            BotPackEntry("diplomat", 80, "even", 2),
            BotPackEntry("coalition_seeker", 65, "careful", 3),
        ],
    ),
    "chaos": BotPack(
        id="chaos",
        version="v1",
        name="Chaos",
        hidden=False,
        entries=[
            BotPackEntry("grudger", 35, "bitter", 0),
            BotPackEntry("leader_pressure", 45, "twitchy", 1),
            BotPackEntry("opportunist", 25, "twitchy", 2),
            BotPackEntry("endgame_sniper", 35, "bitter", 3),
        ],
    ),
    "fixture_zero_floor": BotPack(
        id="fixture_zero_floor",
        version="v1",
        name="Fixture: Zero Floor",
        hidden=True,
        entries=[
            BotPackEntry("leader_pressure", 80, "even", 0),
            BotPackEntry("grudger", 80, "bitter", 1),
        ],
    ),
}


def resolve_pack(pack_id: str) -> BotPack:
    return BOT_PACKS[pack_id]


def pack_profile_choices(*, include_hidden: bool = False) -> list[BotProfileChoice]:
    choices: list[BotProfileChoice] = []
    for pack in BOT_PACKS.values():
        if pack.hidden and not include_hidden:
            continue
        for index, entry in enumerate(pack.entries):
            choices.append(
                BotProfileChoice(
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


def resolve_profile_choice(choice_id: str, *, seed_base: int = 0) -> BotProfile:
    pack_id, index_text = choice_id.split(":", 1)
    pack = resolve_pack(pack_id)
    index = int(index_text)
    entry = pack.entries[index]
    return BotProfile(
        strategy=entry.strategy,
        truthfulness=entry.truthfulness,
        trust_model=entry.trust_model,
        seed=seed_base + entry.seed_offset,
        version=pack.version,
        fixture_pack=pack.id if pack.hidden else None,
    )


def expand_pack(pack_id: str, *, seed_base: int = 0) -> list[BotProfile]:
    pack = resolve_pack(pack_id)
    return [
        BotProfile(
            strategy=entry.strategy,
            truthfulness=entry.truthfulness,
            trust_model=entry.trust_model,
            seed=seed_base + entry.seed_offset,
            version=pack.version,
            fixture_pack=pack.id if pack.hidden else None,
        )
        for entry in pack.entries
    ]


def _choice_label(entry: BotPackEntry) -> str:
    return (
        f"{entry.strategy.replace('_', ' ').title()} · "
        f"{entry.truthfulness}% · {entry.trust_model.title()}"
    )


def _choice_description(entry: BotPackEntry, pack_version: str, index: int) -> str:
    return f"Pack version {pack_version} · slot {index + 1}"
