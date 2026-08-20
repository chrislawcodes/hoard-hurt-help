#!/usr/bin/env python3
"""Report on one finished match: validity first, then the numbers.

    python3 scripts/match_runner/analyse_match.py M_6855

Reads the PUBLIC spectator state (no auth), so it works from anywhere:
``/api/spectator/matches/<id>/state`` — the ``/state`` suffix is required; the
path without it 404s, which looks identical to a quiet healthy match.

VALIDITY IS PRINTED FIRST AND ON PURPOSE. A match where the agents could not
answer still completes and still looks normal: the missing turns are silently
scored as HOARD. Every number below it is meaningless if the talk count is short
or a turn shows almost everyone hoarding at once, so read that block before you
read anything else.
"""
from __future__ import annotations

import collections
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://agentludum.com/api/spectator/matches/{}/state"


def fetch(match_id: str) -> dict:
    """Read one match's public state.

    Falls back to `curl` because a python.org interpreter on macOS ships without
    a CA bundle and dies on the handshake with CERTIFICATE_VERIFY_FAILED. That is
    an interpreter-install problem, not a code problem, and it is not worth
    making whoever runs this debug it — curl uses the system trust store and
    simply works.
    """
    url = BASE.format(match_id)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except (ssl.SSLError, urllib.error.URLError):
        out = subprocess.run(
            ["curl", "-sS", "--max-time", "30", url],
            capture_output=True, text=True, check=True,
        )
        return json.loads(out.stdout)


def classify(turn: dict) -> list[tuple[str, str, str, int]]:
    """Every HURT this turn, tagged with the v9 tier it actually hit."""
    action = {a["agent_id"]: a["action"] for a in turn["actions"]}
    target = {a["agent_id"]: a.get("target_id") for a in turn["actions"]}
    helps = {
        a["agent_id"]: a.get("target_id")
        for a in turn["actions"]
        if a["action"] == "HELP"
    }
    out = []
    for a in turn["actions"]:
        if a["action"] != "HURT":
            continue
        me, victim = a["agent_id"], a.get("target_id")
        if action.get(victim) == "HURT" and target.get(victim) == me:
            tier = "BLOCKED"
        elif helps.get(victim) == me:
            tier = "betrayal"
        elif action.get(victim) == "HELP":
            tier = "helper"
        elif action.get(victim) == "HOARD":
            tier = "hoarder"
        else:
            tier = "attacker (0)"
        out.append((me, victim or "?", tier, a.get("points_delta") or 0))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    d = fetch(sys.argv[1])
    H = d.get("history") or []
    seats = len(d.get("scoreboard") or []) or 8
    expected = len(H) * seats

    acts = [a for t in H for a in t["actions"]]
    talk = sum(len(t.get("messages") or []) for t in H)
    stalled = [t for t in H if sum(1 for a in t["actions"] if a["action"] == "HOARD") >= seats - 2]

    print(f"{d['match_id']}  {d['name']!r}  state={d['state']}  turns={len(H)}")
    print("\n--- VALIDITY (read this first) ---")
    print(f"  moves {len(acts)}/{expected}   talk {talk}/{expected}")
    if stalled:
        print(f"  !! {len(stalled)} turn(s) had nearly everyone hoarding — likely a stalled loop:")
        for t in stalled[:5]:
            print(f"     R{t['round']}T{t['turn']}")
        print("  !! treat every number below as contaminated")
    elif talk < expected:
        print("  !! talk is short of the move count — some agents were not answering")
    else:
        print("  clean: no stalled turns, full talk coverage")

    mix = collections.Counter(a["action"] for a in acts)
    print("\n--- MOVE MIX ---")
    for k in ("HOARD", "HELP", "HURT"):
        print(f"  {k:<6}{mix[k]:>4}  {mix[k] / max(len(acts), 1) * 100:5.1f}%")

    tiers = collections.Counter()
    rows = []
    for t in H:
        for me, victim, tier, delta in classify(t):
            tiers[tier] += 1
            rows.append(f"  R{t['round']}T{t['turn']}  {me} -> {victim}  [{tier}] {delta:+}")
    print("\n--- ATTACKS BY TIER ---")
    if not rows:
        print("  none")
    for k, v in tiers.most_common():
        print(f"  {k:<14}{v}")

    score: dict[str, int] = collections.defaultdict(int)
    rounds: dict[int, dict[str, int]] = {}
    cur = None
    for t in H:
        if t["round"] != cur:
            cur, score = t["round"], collections.defaultdict(int)
        for a in t["actions"]:
            score[a["agent_id"]] += a.get("points_delta") or 0
        rounds[t["round"]] = dict(score)
    ties = 0
    print("\n--- ROUNDS ---")
    for r in sorted(rounds):
        s = rounds[r]
        best = max(s.values())
        winners = sorted(k for k, v in s.items() if v == best)
        if len(winners) > 1:
            ties += 1
        print(f"  R{r}: {best:>3} — {', '.join(winners)}{'  (TIE)' if len(winners) > 1 else ''}")
    if rounds:
        print(f"\n  TIE RATE {ties}/{len(rounds)} = {ties / len(rounds) * 100:.0f}%")

    print("\n--- FINAL ---")
    for x in sorted(d.get("scoreboard") or [], key=lambda z: -z.get("round_wins", 0)):
        print(f"  {x['agent_id']:<22}wins={x.get('round_wins', 0):.2f}")
    print("\nReminder: identical twins have finished up to 2.0 round wins apart, so a")
    print("single match ranks nothing. Trust the rates, not the placings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
