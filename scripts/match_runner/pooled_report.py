#!/usr/bin/env python3
"""Is the ruleset any good? Answered across every match, grouped by rules version.

The questions here cannot be answered by one match and it is worth being blunt
about why: a match is seven rounds, so any per-round percentage moves 14 points
when a single round falls the other way. Three attempts at putting these in the
per-match report produced numbers that read like findings and were noise. They
only mean something pooled.

Grouping by rules version is the point of the whole thing. Comparing a v9 match
with a v10 match without saying so is how a payoff change gets credited with a
difference it did not cause.

The same trap has a second door: the ROSTER. Two v10 matches played by different
presets are not two samples of the same experiment. Swapping one preset changes
who is available to help and who is worth attacking, which moves every rate in
here. Rules version is stamped on the match and easy to group by; the roster is
not stamped anywhere, so it is fingerprinted from the strategy text and any
change within a version is flagged rather than silently averaged.

Contaminated matches are dropped, not down-weighted. A defaulted move is scored
as a HOARD, so including one bends the hoard rate — the exact number a payoff
change is usually trying to move.

usage: pooled_report.py <export.json> [export.json ...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from preset_fidelity import print_fidelity, tally_rules  # noqa: E402


def model_key(d: dict[str, Any]) -> str:
    """Which model the agents played on.

    Pooled beside rules version and roster for the same reason: a Haiku match and
    a Sonnet match are not two samples of one experiment. Mixing them would credit
    a payoff change with a difference the model made.
    """
    models = sorted({p.get("model") or "unset" for p in d["players"] if p.get("strategy_prompt")})
    return ", ".join(models) if models else "unset"


def roster_key(d: dict[str, Any]) -> tuple[str, ...]:
    """Which presets played, from their strategy text.

    The text, not the agent's display name: a name is free-form and mine have
    carried scratch suffixes like "r2" that say nothing about what the agent
    was told to do. Two agents with the same strategy ARE the same competitor
    whatever they are called, and a renamed agent must not read as a new one.
    """
    return tuple(sorted((p.get("strategy_prompt") or "(bot)").strip() for p in d["players"]))


def load(paths: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """Group usable exports by rules version; report what was dropped and why.

    Keyed by match id so the same match handed in twice (a partial download
    beside a complete one) counts once, keeping whichever copy has more rows.
    """
    seen: dict[str, dict] = {}
    dropped: list[str] = []
    for p in paths:
        try:
            with open(p) as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            dropped.append(f"{p}: unreadable ({exc.__class__.__name__})")
            continue
        subs = d.get("submissions") or []
        if not subs:
            dropped.append(f"{p}: no submissions")
            continue
        bad = sum(1 for s in subs if s.get("was_defaulted"))
        if bad:
            mid = d["game"]["id"]
            # Keyed by id: handing in two downloads of the same bad match should
            # say so once, not twice.
            note = f"{mid}: {bad}/{len(subs)} moves filled in by the server"
            if not any(line.startswith(f"{mid}:") for line in dropped):
                dropped.append(note)
            continue
        mid = d["game"]["id"]
        if mid in seen and len(seen[mid]["submissions"]) >= len(subs):
            continue
        seen[mid] = d
    by_version: dict[str, list[dict]] = defaultdict(list)
    for d in seen.values():
        by_version[d["game"].get("rules_version", "?")].append(d)
    return by_version, dropped


def round_winners(d: dict[str, Any]) -> list[tuple[int, list[str], Counter]]:
    """(round, winners, the winners' combined move mix) for each round."""
    subs = d["submissions"]
    last = max(s["turn"] for s in subs)
    score: dict[int, dict[str, int]] = defaultdict(dict)
    for s in subs:
        if s["turn"] == last:
            score[s["round"]][s["agent_id"]] = s["round_score_after"]
    out = []
    for r, standings in sorted(score.items()):
        top = max(standings.values())
        winners = [a for a, v in standings.items() if v == top]
        mix: Counter[str] = Counter()
        for s in subs:
            if s["round"] == r and s["agent_id"] in winners:
                mix[s["action"]] += 1
        out.append((r, winners, mix))
    return out


def lead_curve(matches: list[dict]) -> list[tuple[int, float, int]]:
    """How often the leader at turn N goes on to win the round.

    This is the honest version of "are early turns meaningful". An earlier
    attempt asked whether a round was mathematically decided; it answered "never"
    for every round of every match, because one betrayal can swing 22 points and
    no realistic gap is ever formally safe.
    """
    rounds: list[list[set[str]]] = []
    for d in matches:
        subs = d["submissions"]
        turns = max(s["turn"] for s in subs)
        at: dict[tuple[int, int], dict[str, int]] = defaultdict(dict)
        for s in subs:
            at[(s["round"], s["turn"])][s["agent_id"]] = s["round_score_after"]
        for r in range(1, max(s["round"] for s in subs) + 1):
            seq = []
            for t in range(1, turns + 1):
                standings = at[(r, t)]
                top = max(standings.values())
                seq.append({a for a, v in standings.items() if v == top})
            rounds.append(seq)
    if not rounds:
        return []
    turns = len(rounds[0])
    return [
        (t + 1, sum(1 for seq in rounds if seq[t] & seq[-1]) / len(rounds) * 100, len(rounds))
        for t in range(turns)
    ]


def clinched_early(d: dict[str, Any]) -> bool:
    """Did the leader become uncatchable before the final round?"""
    wins: Counter[str] = Counter()
    rounds = round_winners(d)
    total = len(rounds)
    for i, (_r, winners, _mix) in enumerate(rounds):
        for a in winners:
            wins[a] += 1 / len(winners)
        left = total - (i + 1)
        if left == 0:
            break
        ranked = wins.most_common()
        second = ranked[1][1] if len(ranked) > 1 else 0
        if ranked[0][1] > second + left:
            return True
    return False


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: pooled_report.py <export.json> [export.json ...]", file=sys.stderr)
        return 2
    by_version, dropped = load(sys.argv[1:])
    if not by_version:
        print("no usable matches", file=sys.stderr)
        for line in dropped:
            print(f"  dropped {line}", file=sys.stderr)
        return 1

    total = sum(len(v) for v in by_version.values())
    print(f"POOLED — {total} clean matches, {len(by_version)} rules version(s)")
    for line in dropped:
        print(f"   dropped {line}")

    # One self-contained block per ruleset. Nothing is averaged across versions:
    # a combined figure would be the average of two different games.
    for ver in sorted(by_version, reverse=True):
        matches = by_version[ver]
        print(f"\n{'=' * 62}")
        print(f"RULES {ver} — {len(matches)} match(es)")

        # Roster check. A preset swap changes who can be helped and who is worth
        # hitting, so it moves every rate below. Flagged, never quietly pooled.
        models: dict[str, list[str]] = defaultdict(list)
        for d in matches:
            models[model_key(d)].append(d["game"]["id"])
        if len(models) > 1:
            print(f"   MODELS DIFFER — {len(models)} across this version:")
            for mdl, ids in models.items():
                print(f"     {mdl}: {', '.join(ids)}")
        elif next(iter(models)) != "unset":
            print(f"   model: {next(iter(models))}")

        rosters: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for d in matches:
            rosters[roster_key(d)].append(d["game"]["id"])
        if len(rosters) > 1:
            print(f"   ROSTER CHANGED — {len(rosters)} different line-ups in this version.")
            print("   The numbers below average across them; treat them as indicative only.")
            groups = list(rosters.items())
            base = set(groups[0][0])
            for key, ids in groups:
                added = set(key) - base
                gone = base - set(key)
                note = ""
                if added or gone:
                    note = f"   (+{len(added)} / -{len(gone)} vs {groups[0][1][0]})"
                print(f"     {', '.join(ids)}{note}")
        # Silence when the line-up held. The same roster is the expected case, so
        # saying so every run trains the reader to skip the line that matters.

        mix: Counter[str] = Counter()
        for d in matches:
            for s_ in d["submissions"]:
                mix[s_["action"]] += 1
        n = sum(mix.values())
        print(f"\n   move mix        hoard {mix['HOARD'] / n * 100:.0f}%   "
              f"help {mix['HELP'] / n * 100:.0f}%   hurt {mix['HURT'] / n * 100:.0f}%   ({n} moves)")

        curve = lead_curve(matches)
        if curve:
            cells = "".join(f"{'T' + str(t):>6}" for t, _, _ in curve)
            vals = "".join(f"{pct:>5.0f}%" for _, pct, _ in curve)
            print("\n   leading here wins the round")
            print(f"     {cells}")
            print(f"     {vals}   ({curve[0][2]} rounds)")

        style: Counter[str] = Counter()
        for d in matches:
            for _r, _w, m in round_winners(d):
                if m:
                    style[max(m, key=lambda k: m[k])] += 1
        tot = sum(style.values())
        print(f"\n   rounds won by   mostly hoard {style['HOARD']}   "
              f"mostly help {style['HELP']}   mostly hurt {style['HURT']}   (of {tot})")

        early = sum(1 for d in matches if clinched_early(d))
        print(f"   decided before the final round   {early} of {len(matches)}")

        # The full table here, not just failures: a rule that only gets two or
        # three chances a match is unreadable alone and only means something
        # once the pool adds them up.
        print("\n   preset fidelity")
        print_fidelity(tally_rules(matches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
