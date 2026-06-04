"""Preset Sim profiles and helper naming utilities."""

from __future__ import annotations

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

HISTORICAL_SIM_NAME_POOL: tuple[str, ...] = (
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


def sim_presets() -> list[SimPreset]:
    return list(SIM_PRESETS)


def sim_preset_by_id(preset_id: str) -> SimPreset | None:
    return next((preset for preset in SIM_PRESETS if preset.id == preset_id), None)


def build_sim_bot_name(
    *,
    used_names: set[str] | None = None,
    name_index: int = 0,
) -> str:
    """Build the default display name for a preset Sim bot."""
    taken = used_names if used_names is not None else set()
    for offset in range(len(HISTORICAL_SIM_NAME_POOL)):
        candidate = HISTORICAL_SIM_NAME_POOL[
            (name_index + offset) % len(HISTORICAL_SIM_NAME_POOL)
        ]
        if candidate not in taken:
            return candidate
    suffix = 1
    while True:
        candidate = f"Leader {suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1


def allocate_default_sim_names(
    count: int,
    *,
    used_names: set[str] | None = None,
) -> list[str]:
    """Pick default Sim names from the historical leader pool."""
    taken = set(used_names or set())
    names: list[str] = []
    for index in range(count):
        name = build_sim_bot_name(used_names=taken, name_index=index)
        names.append(name)
        taken.add(name)
    return names
