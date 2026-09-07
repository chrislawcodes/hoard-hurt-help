"""PD play-by-play narrative engine: the deterministic per-turn headline.

The replay feed shows a one-line "play-by-play" headline for each turn — who
betrayed whom, which pacts locked in, who got ganged up on, who took the lead.
This module holds that narrative engine: the phrase banks, the number-to-word
helper, and the deterministic beat selection/rendering in `_turn_headline`.

Split out of `viewer.py` verbatim so the payload builder there carries only the
rc_data/replay shaping, not the narration. `viewer.py` imports `_turn_headline`
from here.
"""

from __future__ import annotations

_HEADLINE_PHRASES: dict[str, tuple[str, ...]] = {
    "betray": (
        "{a} turns on former ally {b}",
        "{a} breaks faith with {b}",
        "{a} stabs {b} in the back",
        "{a} abandons the pact with {b}",
    ),
    "pact": (
        "{a} and {b} lock in a pact (+{v} each)",
        "{a} and {b} shake hands — +{v} apiece",
        "a fresh alliance forms: {a} and {b} (+{v} each)",
    ),
    "gangup": (
        "{n} bots pile on {t}",
        "the table turns on {t} — {n} strikes land",
        "{n} bots gang up on {t}",
    ),
    "revenge": (
        "{n} bots round on {t} — payback for the betrayal",
        "{t} pays for the betrayal as {n} pile in",
    ),
    "lead": (
        "that hands {a} the lead",
        "{a} seizes first place",
        "that vaults {a} to the top",
    ),
    "swing": (
        "{a} clobbers {b} ({d})",
        "{a}'s strike sends {b} reeling ({d})",
    ),
    "residual": (
        "the other {n} just hoard",
        "{n} more keep their heads down",
        "the remaining {n} bank quietly",
    ),
    "quiet": (
        "a quiet turn — most of the table just hoards",
        "a calm turn; almost everyone banks a coin",
    ),
}

_NUM_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
)


def _num_word(n: int) -> str:
    return _NUM_WORDS[n] if 0 <= n < len(_NUM_WORDS) else str(n)


def _mutual_pairs(actions: list[dict]) -> set[frozenset[str]]:
    return {
        frozenset((a["agent_id"], a["target_id"]))
        for a in actions
        if a.get("mutual") and a["target_id"]
    }


def _pact_values(actions: list[dict]) -> dict[frozenset[str], int]:
    """This turn's per-side pact value, by pair.

    ``display_delta`` is already the resolver's real, mode-aware per-side
    payout for a mutual HELP (set alongside ``mutual`` in ``viewer.py``), so
    reading it here — instead of a second hardcoded number — means the
    headline can never advertise a payout the game didn't actually pay.
    """
    return {
        frozenset((a["agent_id"], a["target_id"])): a["display_delta"]
        for a in actions
        if a.get("mutual") and a["target_id"]
    }


def _phrase(kind: str, idx: int) -> str:
    """Pick this beat kind's phrase deterministically from its bank."""
    bank = _HEADLINE_PHRASES[kind]
    return bank[idx % len(bank)]


def _render_beat(kind: str, idx: int, b: dict) -> str:
    """Render one beat as a capitalized sentence fragment (no trailing period)."""
    s = _phrase(kind, idx).format(
        a=b.get("a"),
        b=b.get("b"),
        t=b.get("t"),
        n=_num_word(b.get("n", 0)),
        d=b.get("d", ""),
        v=b.get("v", ""),
    )
    return s[0].upper() + s[1:]


def _betrayal_beats(actions: list[dict]) -> list[tuple[int, dict]]:
    """One beat per betrayal this turn, prioritized by how hard it hit."""
    beats: list[tuple[int, dict]] = []
    for a in actions:
        if a.get("betrayal"):
            beats.append(
                (
                    100 + abs(a.get("display_delta") or 0),
                    {"kind": "betray", "a": a["agent_id"], "b": a["target_id"]},
                )
            )
    return beats


def _pact_beats(actions: list[dict], prev_actions: list[dict]) -> list[tuple[int, dict]]:
    """One beat per newly-formed pact this turn (a mutual HELP that wasn't
    already mutual last turn)."""
    beats: list[tuple[int, dict]] = []
    prev_pairs = _mutual_pairs(prev_actions)
    pact_values = _pact_values(actions)
    for pair in _mutual_pairs(actions):
        if pair not in prev_pairs:
            x, y = sorted(pair)
            beats.append((70, {"kind": "pact", "a": x, "b": y, "v": pact_values[pair]}))
    return beats


def _pileon_beats(actions: list[dict], prev_actions: list[dict]) -> list[tuple[int, dict]]:
    """One beat per target hit by two or more attackers this turn — "revenge"
    if the target betrayed someone last turn, "gangup" otherwise."""
    beats: list[tuple[int, dict]] = []
    hits: dict[str, list[str]] = {}
    for a in actions:
        if a["action"] == "HURT" and a["target_id"]:
            hits.setdefault(a["target_id"], []).append(a["agent_id"])
    prev_betrayers = {a["agent_id"] for a in prev_actions if a.get("betrayal")}
    for target, hitters in hits.items():
        if len(hitters) >= 2:
            kind = "revenge" if target in prev_betrayers else "gangup"
            beats.append((75 + len(hitters), {"kind": kind, "t": target, "n": len(hitters)}))
    return beats


def _swing_beat(actions: list[dict]) -> tuple[int, dict] | None:
    """The turn's biggest non-betrayal HURT, if it landed hard enough (>= 4)
    to be worth its own beat."""
    swing = max(
        (
            a
            for a in actions
            if a["action"] == "HURT" and a["target_id"] and not a.get("betrayal")
        ),
        key=lambda a: abs(a.get("display_delta") or 0),
        default=None,
    )
    if swing is not None and abs(swing.get("display_delta") or 0) >= 4:
        d = swing.get("display_delta") or 0
        return (60, {"kind": "swing", "a": swing["agent_id"], "b": swing["target_id"], "d": str(d)})
    return None


def _lead_change_beat(leader: str | None, prev_leader: str | None) -> tuple[int, dict] | None:
    """A lead-change beat, if the in-round leader actually changed this turn."""
    if leader and prev_leader and leader != prev_leader:
        return (90, {"kind": "lead", "a": leader})
    return None


def _collect_beats(
    actions: list[dict],
    prev_actions: list[dict],
    leader: str | None,
    prev_leader: str | None,
) -> list[tuple[int, dict]]:
    """This turn's candidate beats — betrayals, newly-formed pacts, gangup/
    revenge pile-ons, the biggest non-betrayal swing, and a lead change —
    each tagged with the priority that decides which get told."""
    beats: list[tuple[int, dict]] = []
    beats.extend(_betrayal_beats(actions))
    beats.extend(_pact_beats(actions, prev_actions))
    beats.extend(_pileon_beats(actions, prev_actions))

    swing_beat = _swing_beat(actions)
    if swing_beat is not None:
        beats.append(swing_beat)

    lead_beat = _lead_change_beat(leader, prev_leader)
    if lead_beat is not None:
        beats.append(lead_beat)

    return beats


def _select_beats(beats: list[tuple[int, dict]]) -> tuple[list[dict], dict | None]:
    """Sort candidate beats by priority, then greedily take up to two whose
    actors don't overlap — pulling the lead-change beat out on its own,
    since it gets folded into a chosen sentence or said standalone."""
    beats.sort(key=lambda b: -b[0])

    used: set[str] = set()
    chosen: list[dict] = []
    lead_beat: dict | None = None
    for _prio, b in beats:
        if b["kind"] == "lead":
            lead_beat = lead_beat or b
            continue
        actors = [b[k] for k in ("a", "b", "t") if b.get(k)]
        if any(x in used for x in actors):
            continue
        used.update(actors)
        chosen.append(b)
        if len(chosen) == 2:
            break
    return chosen, lead_beat


def _render_sentences(chosen: list[dict], lead_beat: dict | None, ordinal: int) -> list[str]:
    """Render the chosen beats as sentences, folding a lead change into the
    first one when its actor differs, or appending it as its own sentence
    otherwise."""
    sentences: list[str] = []
    for i, b in enumerate(chosen):
        s = _render_beat(b["kind"], ordinal + i, b)
        if i == 0 and lead_beat is not None and lead_beat["a"] != b.get("a"):
            s += " — " + _phrase("lead", ordinal).format(a=lead_beat["a"])
            lead_beat = None
        sentences.append(s + ".")
    if lead_beat is not None:
        sentences.append(_render_beat("lead", ordinal, lead_beat) + ".")
    return sentences


def _finish_headline(sentences: list[str], actions: list[dict], ordinal: int) -> str:
    """No beats worth telling → a quiet-turn line; otherwise append a
    residual-hoarders trailer when at least half the table just hoarded,
    then join every sentence into the turn's headline."""
    if not sentences:
        return _render_beat("quiet", ordinal, {}) + "."

    hoards = sum(1 for a in actions if a["action"] == "HOARD")
    if hoards >= len(actions) / 2:
        sentences.append(_render_beat("residual", ordinal, {"n": hoards}) + ".")
    return " ".join(sentences)


def _turn_headline(
    actions: list[dict],
    prev_actions: list[dict],
    leader: str | None,
    prev_leader: str | None,
    ordinal: int,
) -> str:
    """A deterministic one-line play-by-play for a turn."""
    beats = _collect_beats(actions, prev_actions, leader, prev_leader)
    chosen, lead_beat = _select_beats(beats)
    sentences = _render_sentences(chosen, lead_beat, ordinal)
    return _finish_headline(sentences, actions, ordinal)
