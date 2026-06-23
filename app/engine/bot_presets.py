"""Preset bot profiles and helper naming utilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotPreset:
    id: str
    name: str
    description: str
    strategy: str
    truthfulness: int
    trust_model: str
    seed_offset: int


# Display name and behavior were reworked; internal strategy ids are kept stable
# (see app/engine/bots/strategies.py) so saved bots and tests don't churn.
BOT_PRESETS: tuple[BotPreset, ...] = (
    BotPreset(
        id="coalition_seeker",
        name="Coalition Seeker",
        description="Reaches out early, then builds mutual-help partnerships and rewards its helpers.",
        strategy="coalition_seeker",
        truthfulness=90,
        trust_model="even",
        seed_offset=0,
    ),
    BotPreset(
        id="pragmatist",
        name="Pragmatist",
        description="Plays nice to build a partnership, then betrays at the buzzer to take the win.",
        strategy="pragmatist",
        truthfulness=80,
        trust_model="even",
        seed_offset=8,
    ),
    BotPreset(
        id="loyal_partner",
        name="Loyal Partner",
        description="Sticks with proven partners; throws out the occasional test help to find one.",
        strategy="loyal_partner",
        truthfulness=80,
        trust_model="open",
        seed_offset=1,
    ),
    BotPreset(
        id="grudger",
        name="Long Memory",
        description="Remembers both: repays helpers, punishes attackers, gangs up on a runaway leader.",
        strategy="grudger",
        truthfulness=80,
        trust_model="bitter",
        seed_offset=2,
    ),
    BotPreset(
        id="leader_pressure",
        name="Giant Slayer",
        description="Builds its own score, but drops everything to hit anyone running away with the lead.",
        strategy="leader_pressure",
        truthfulness=80,
        trust_model="careful",
        seed_offset=3,
    ),
    BotPreset(
        id="opportunist",
        name="Opportunist",
        description="Works the standings: cooperates when it pays, claws at the leader when behind.",
        strategy="opportunist",
        truthfulness=80,
        trust_model="twitchy",
        seed_offset=4,
    ),
    BotPreset(
        id="endgame_sniper",
        name="The Closer",
        description="Plays patient and friendly, then strikes the leader in the final turns.",
        strategy="endgame_sniper",
        truthfulness=80,
        trust_model="even",
        seed_offset=5,
    ),
    BotPreset(
        id="diplomat",
        name="Instigator",
        description="Rewards aggression — hands help to whoever's attacking, stirring up the table.",
        strategy="diplomat",
        truthfulness=80,
        trust_model="open",
        seed_offset=6,
    ),
    BotPreset(
        id="crowd_follower",
        name="Crowd Follower",
        description="Copies whatever the table is doing, but sticks with anyone who helps it.",
        strategy="crowd_follower",
        truthfulness=80,
        trust_model="careful",
        seed_offset=7,
    ),
)

HISTORICAL_BOT_NAME_POOL: tuple[str, ...] = (
    # Africa and North Africa
    "Cleopatra",
    "Hannibal",
    "Dido",
    "Mansa Musa",
    "Ramesses II",
    "Hatshepsut",
    "Nefertiti",
    "Tutankhamun",
    "Shaka",
    "Haile Selassie",
    "Askia",
    "Menelik",
    "Nelson Mandela",
    "Nzinga",
    "Amanirenas",
    "Kwame Nkrumah",
    # Middle East
    "Cyrus",
    "Darius",
    "Xerxes",
    "Saladin",
    "Khalid",
    "Suleiman",
    "Nebuchadnezzar",
    "Harun al Rashid",
    "Mehmed",
    "Zenobia",
    "Hammurabi",
    "Sargon",
    "Gilgamesh",
    "Ataturk",
    "Nader Shah",
    "Baybars",
    # Europe
    "Caesar",
    "Augustus",
    "Scipio",
    "Trajan",
    "Constantine",
    "Justinian",
    "Theodora",
    "Nero",
    "Alexander",
    "Leonidas",
    "Pericles",
    "Themistocles",
    "Boudica",
    "Charlemagne",
    "Joan of Arc",
    "Napoleon",
    "Nelson",
    "Wellington",
    "Marlborough",
    "Elizabeth",
    "Victoria",
    "Peter the Great",
    "Catherine the Great",
    "Frederick",
    "Gustavus Adolphus",
    "Cromwell",
    "Churchill",
    "Bismarck",
    "Eisenhower",
    "Grant",
    "Zhukov",
    "Suvorov",
    "Alfred the Great",
    "William the Conqueror",
    "Isabella",
    "Maria Theresa",
    "Louis XIV",
    "Henry VIII",
    "Richard the Lionheart",
    "William Wallace",
    "Robert the Bruce",
    "Charles V",
    "Charles Martel",
    "El Cid",
    "Francis Drake",
    "de Gaulle",
    "Rommel",
    "Patton",
    # East Asia
    "Sun Tzu",
    "Qin Shi Huang",
    "Wu Zetian",
    "Kublai Khan",
    "Yi Sun Sin",
    "Tokugawa",
    "Oda Nobunaga",
    "Toyotomi Hideyoshi",
    "Meiji",
    "Mao",
    "Chiang Kai Shek",
    "Zhuge Liang",
    "Cao Cao",
    "Sejong",
    "Kangxi",
    # South Asia
    "Ashoka",
    "Chandragupta",
    "Akbar",
    "Gandhi",
    "Shivaji",
    "Tipu Sultan",
    "Rani Lakshmibai",
    "Indira Gandhi",
    # Central Asia
    "Genghis Khan",
    "Subutai",
    "Tamerlane",
    "Attila",
    "Tomyris",
    "Alp Arslan",
    # Southeast Asia and Oceania
    "Gajah Mada",
    "Kamehameha",
    "Ramkhamhaeng",
    "Trung Trac",
    "Ho Chi Minh",
    "Sukarno",
    # Americas
    "Washington",
    "Lincoln",
    "Montezuma",
    "Pachacuti",
    "Sitting Bull",
    "Geronimo",
    "Tecumseh",
    "Bolivar",
    "Toussaint",
    "Teddy Roosevelt",
)


def bot_presets() -> list[BotPreset]:
    return list(BOT_PRESETS)


def bot_preset_by_id(preset_id: str) -> BotPreset | None:
    return next((preset for preset in BOT_PRESETS if preset.id == preset_id), None)


def build_bot_name(
    *,
    used_names: set[str] | None = None,
    name_index: int = 0,
) -> str:
    """Build the default display name for a preset bot."""
    taken = used_names if used_names is not None else set()
    for offset in range(len(HISTORICAL_BOT_NAME_POOL)):
        candidate = HISTORICAL_BOT_NAME_POOL[
            (name_index + offset) % len(HISTORICAL_BOT_NAME_POOL)
        ]
        if candidate not in taken:
            return candidate
    suffix = 1
    while True:
        candidate = f"Leader {suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1


def allocate_default_bot_names(
    count: int,
    *,
    used_names: set[str] | None = None,
) -> list[str]:
    """Pick default bot names from the historical leader pool."""
    taken = set(used_names or set())
    names: list[str] = []
    for index in range(count):
        name = build_bot_name(used_names=taken, name_index=index)
        names.append(name)
        taken.add(name)
    return names
