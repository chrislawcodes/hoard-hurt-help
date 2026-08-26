#!/usr/bin/env python3
"""Does each agent actually follow the rules its preset gave it?

Presets drift and nothing notices. Sandbagger is told not to attack in the first
half and has attacked in first halves across three matches; Hoarder is told to
HOARD by default and spent M_7453 helping 25 times out of 35. Both were found by
reading rows by hand. A preset nobody checks is a preset that has quietly become
something else, which makes every roster question — is this one distinct, is it
earning its slot — unanswerable.

Only rules the export can see are here. The export carries the action, the
target, the round and turn, and the score after; it does not carry who offered
what in talk. So "repay every promise" is not testable and is absent rather than
approximated.

THE DRIFT GUARD. These rules are hand-copied out of strategy.py, so this file is
a second home for something that already has one — the exact shape this repo's
worst bugs take. Each rule therefore carries the snippet of preset text it was
written against, and is tested against the strategy stored on the agent. Reword
the preset and the check DISABLES ITSELF and says so, instead of quietly
reporting compliance with a rule nobody wrote any more.

usage: preset_fidelity.py <export.json> [export.json ...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from typing import Any, Callable

# (preset name, snippet that must still be in the preset, rule as shown, checker)
# The checker gets that agent's rows plus the whole match, and returns
# (followed, opportunities). Returning (0, 0) means the rule never came up.
Rows = list[dict[str, Any]]
Check = Callable[[Rows, Rows, int], tuple[int, int]]


def _by_turn(match: Rows) -> dict[tuple[int, int], Rows]:
    out: dict[tuple[int, int], Rows] = defaultdict(list)
    for s in match:
        out[(s["round"], s["turn"])].append(s)
    return out


def _hurt_me(match: Rows, me: str) -> set[str]:
    """Everyone who has attacked me at any point."""
    return {s["agent_id"] for s in match if s["action"] == "HURT" and s.get("target_id") == me}


def always_hurt(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    return sum(1 for s in rows if s["action"] == "HURT"), len(rows)


def never_help(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    return sum(1 for s in rows if s["action"] != "HELP"), len(rows)


def no_hurt_first_half(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    early = [s for s in rows if s["round"] <= half]
    return sum(1 for s in early if s["action"] != "HURT"), len(early)


def hurt_late_in_round(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    """From halfway on, attacks should land in the last two turns of a round."""
    last = max(s["turn"] for s in match)
    late = [s for s in rows if s["round"] > half and s["action"] == "HURT"]
    return sum(1 for s in late if s["turn"] >= last - 1), len(late)


def only_hurt_my_helper(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    """Never HURT a player who isn't HELPing you — judged on that same turn."""
    turns = _by_turn(match)
    hits = [s for s in rows if s["action"] == "HURT" and s.get("target_id")]
    ok = 0
    for s in hits:
        helping_me = {
            o["agent_id"]
            for o in turns[(s["round"], s["turn"])]
            if o["action"] == "HELP" and o.get("target_id") == s["agent_id"]
        }
        if s["target_id"] in helping_me:
            ok += 1
    return ok, len(hits)


def answer_a_betrayal(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    """Anyone who attacked me should be attacked back at some point."""
    me = rows[0]["agent_id"]
    attackers = _hurt_me(match, me)
    if not attackers:
        return 0, 0
    struck = {s.get("target_id") for s in rows if s["action"] == "HURT"}
    return len(attackers & struck), len(attackers)


def never_deal_again(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    """Once someone betrays you, never HELP them again."""
    me = rows[0]["agent_id"]
    order = sorted(match, key=lambda s: (s["round"], s["turn"]))
    burned: set[str] = set()
    ok = total = 0
    for s in order:
        if s["action"] == "HURT" and s.get("target_id") == me:
            burned.add(s["agent_id"])
        if s["agent_id"] == me and s["action"] == "HELP" and s.get("target_id") in burned:
            total += 1
        elif s["agent_id"] == me and s["action"] == "HELP":
            total += 1
            ok += 1
    return ok, total


def never_help_top_half(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    """HELP should never go to someone in the top half of the round standings."""
    helps = [s for s in rows if s["action"] == "HELP" and s.get("target_id")]
    if not helps:
        return 0, 0
    ok = 0
    for s in helps:
        prior = [o for o in match if (o["round"], o["turn"]) < (s["round"], s["turn"])]
        if not prior:
            ok += 1  # nobody is ahead before the first turn
            continue
        standing: dict[str, int] = {}
        for o in prior:
            standing[o["agent_id"]] = o["round_score_after"]
        ranked = sorted(standing, key=lambda a: -standing[a])
        if s["target_id"] in ranked[len(ranked) // 2:]:
            ok += 1
    return ok, len(helps)


def hoard_by_default(rows: Rows, match: Rows, half: int) -> tuple[int, int]:
    mix = Counter(s["action"] for s in rows)
    return mix["HOARD"], len(rows)


# Seat names are free text, and mine have carried scratch suffixes ("TfT r2",
# "Headhunter v2"), so a seat cannot be matched to its preset by name alone.
# The export also gives no join between `players` (numeric ids + strategy text)
# and `submissions` (seat names), so the preset text confirms the rule is
# current while these aliases find the rows.
SEAT_ALIASES: dict[str, tuple[str, ...]] = {
    "Tit-for-Tat": ("tft", "tit"),
    "Underdog's Champion": ("champion", "underdog"),
    "Headhunter": ("headhunter",),
    "Sandbagger": ("sandbagger",),
    "Turncoat": ("turncoat",),
    "Dealmaker": ("dealmaker",),
    "Hoarder": ("hoarder",),
}


RULES: list[tuple[str, str, str, Check]] = [
    ("Headhunter", "HURT someone every single turn", "HURT every turn", always_hurt),
    ("Headhunter", "Never HELP", "never HELP", never_help),
    ("Sandbagger", "never HURT", "no HURT in the first half", no_hurt_first_half),
    ("Sandbagger", "in the last two turns of a round", "late-round strikes only", hurt_late_in_round),
    ("Turncoat", "Never HURT a player who isn't HELPing you", "only HURT a helper", only_hurt_my_helper),
    ("Tit-for-Tat", "HURT them as soon as the hit will not drop them to zero", "answer a betrayal", answer_a_betrayal),
    ("Dealmaker", "never deal with them again", "never HELP a betrayer again", never_deal_again),
    ("Underdog's Champion", "Never HELP anyone in the top half", "never HELP the top half", never_help_top_half),
    ("Hoarder", "HOARD by default", "HOARD by default", hoard_by_default),
]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: preset_fidelity.py <export.json> [export.json ...]", file=sys.stderr)
        return 2

    # preset -> rule -> [followed, opportunities, still_current]
    tally: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 1])
    seen: set[str] = set()
    for path in sys.argv[1:]:
        with open(path) as fh:
            d = json.load(fh)
        mid = d["game"]["id"]
        if mid in seen or any(s.get("was_defaulted") for s in d["submissions"]):
            continue
        seen.add(mid)
        subs = d["submissions"]
        half = max(s["round"] for s in subs) // 2
        strategy = {p["agent_id"]: (p.get("strategy_prompt") or "") for p in d["players"]}
        rows_by: dict[str, Rows] = defaultdict(list)
        for s in subs:
            rows_by[s["agent_id"]].append(s)

        for preset, snippet, label, check in RULES:
            aliases = SEAT_ALIASES.get(preset, (preset.lower(),))
            seat = next((a for a in rows_by if any(x in a.lower() for x in aliases)), None)
            if seat is None or not rows_by[seat]:
                continue
            key = (preset, label)
            # Drift guard: find this preset's text among the players and confirm
            # the rule below is still the rule it was written against.
            text = next((t for t in strategy.values() if f"Strategy: {preset}" in t), "")
            if text and snippet not in text:
                tally[key][2] = 0  # preset reworded — this check is out of date
                continue
            ok, n = check(rows_by[seat], subs, half)
            tally[key][0] += ok
            tally[key][1] += n

    print(f"PRESET FIDELITY — {len(seen)} clean match(es)\n")
    print(f"   {'preset':<22}{'rule':<30}{'followed':>10}")
    for (preset, label), (ok, n, current) in tally.items():
        if not current:
            print(f"   {preset:<22}{label:<30}{'rule reworded — not tested':>10}")
        elif n == 0:
            print(f"   {preset:<22}{label:<30}{'never came up':>10}")
        else:
            flag = "   <-" if ok / n < 0.8 else ""
            print(f"   {preset:<22}{label:<30}{ok / n * 100:>9.0f}%  ({ok}/{n}){flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
