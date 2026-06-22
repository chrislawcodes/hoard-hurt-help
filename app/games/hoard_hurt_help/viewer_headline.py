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
        "{a} and {b} lock in a pact (+8 each)",
        "{a} and {b} shake hands — +8 apiece",
        "a fresh alliance forms: {a} and {b} (+8 each)",
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


def _turn_headline(
    actions: list[dict],
    prev_actions: list[dict],
    leader: str | None,
    prev_leader: str | None,
    ordinal: int,
) -> str:
    """A deterministic one-line play-by-play for a turn."""

    def phrase(kind: str, idx: int) -> str:
        bank = _HEADLINE_PHRASES[kind]
        return bank[idx % len(bank)]

    beats: list[tuple[int, dict]] = []

    for a in actions:
        if a.get("betrayal"):
            beats.append(
                (
                    100 + abs(a.get("display_delta") or 0),
                    {"kind": "betray", "a": a["agent_id"], "b": a["target_id"]},
                )
            )

    prev_pairs = _mutual_pairs(prev_actions)
    for pair in _mutual_pairs(actions):
        if pair not in prev_pairs:
            x, y = sorted(pair)
            beats.append((70, {"kind": "pact", "a": x, "b": y}))

    hits: dict[str, list[str]] = {}
    for a in actions:
        if a["action"] == "HURT" and a["target_id"]:
            hits.setdefault(a["target_id"], []).append(a["agent_id"])
    prev_betrayers = {a["agent_id"] for a in prev_actions if a.get("betrayal")}
    for target, hitters in hits.items():
        if len(hitters) >= 2:
            kind = "revenge" if target in prev_betrayers else "gangup"
            beats.append((75 + len(hitters), {"kind": kind, "t": target, "n": len(hitters)}))

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
        beats.append(
            (
                60,
                {"kind": "swing", "a": swing["agent_id"], "b": swing["target_id"], "d": str(d)},
            )
        )

    if leader and prev_leader and leader != prev_leader:
        beats.append((90, {"kind": "lead", "a": leader}))

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

    def render(kind: str, idx: int, b: dict) -> str:
        s = phrase(kind, idx).format(
            a=b.get("a"),
            b=b.get("b"),
            t=b.get("t"),
            n=_num_word(b.get("n", 0)),
            d=b.get("d", ""),
        )
        return s[0].upper() + s[1:]

    sentences: list[str] = []
    for i, b in enumerate(chosen):
        s = render(b["kind"], ordinal + i, b)
        if i == 0 and lead_beat is not None and lead_beat["a"] != b.get("a"):
            s += " — " + phrase("lead", ordinal).format(a=lead_beat["a"])
            lead_beat = None
        sentences.append(s + ".")
    if lead_beat is not None:
        sentences.append(render("lead", ordinal, lead_beat) + ".")

    if not sentences:
        return render("quiet", ordinal, {}) + "."

    hoards = sum(1 for a in actions if a["action"] == "HOARD")
    if hoards >= len(actions) / 2:
        sentences.append(render("residual", ordinal, {"n": hoards}) + ".")
    return " ".join(sentences)
