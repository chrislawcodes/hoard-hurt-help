#!/usr/bin/env python3
"""One match, two questions: can I trust this, and what happened?

Reads an admin export (`/api/admin/matches/<id>/export.json`), which is the only
source that carries `was_defaulted` and `thinking`. The public spectator feed
cannot answer either question — a defaulted move is recorded as a HOARD and
carries a talk message, so an agent that stopped playing looks exactly like one
that turned cautious. M_7360 read as "the field converged on hoarding in round
four" until the export showed 42 filled-in moves.

Deliberately NOT here: round shape, clinch timing, variety of winners. Those are
ruleset questions, and one match is seven rounds — enough for a number to move
14 percentage points on noise alone. They live in pooled_report.py, which reads
every export it can find. Three attempts at putting them here all produced
figures that looked like findings and were not.

usage: match_report.py <export.json>
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from preset_fidelity import print_fidelity, tally_rules  # noqa: E402


def validity(d: dict[str, Any]) -> None:
    """Three percentages, no verdict — the reader judges.

    They are separate because they fail separately: M_7360 came out 88% / 75% /
    68%, and that spread said the agents degraded (losing their reasoning first,
    then their chat, then failing to move at all) rather than dropping dead. One
    combined number would have hidden that.

    `chat` carries no verdict on purpose: saying nothing is a legitimate move,
    so a low number is information rather than a fault.
    """
    subs = d["submissions"]
    n = len(subs)
    chosen = sum(1 for s in subs if not s.get("was_defaulted"))
    chat = sum(1 for s in subs if (s.get("message") or "").strip())
    think = sum(1 for s in subs if (s.get("thinking") or "").strip())
    # A bot holds no strategy text; in an admin export every real agent's is present.
    bots = sum(1 for p in d["players"] if not p.get("strategy_prompt"))
    agents = len(d["players"]) - bots
    rounds = max(s["round"] for s in subs)
    turns = max(s["turn"] for s in subs)

    print("1. VALIDITY")
    print(f"   action chosen   {chosen / n * 100:5.1f}%  ({chosen}/{n})")
    print(f"   chat            {chat / n * 100:5.1f}%  ({chat}/{n})")
    print(f"   thinking        {think / n * 100:5.1f}%  ({think}/{n})")
    print(f"   {d['game']['state']}   {rounds}x{turns}   {agents} agents, {bots} bots")


def result(d: dict[str, Any]) -> None:
    """Round wins, the score in every round, what the agent did, and what it took.

    Round wins are counted from the turn rows rather than read off `players`,
    because the two halves of the export do not join: `players` keys by numeric
    agent id and `submissions` by seat name, with nothing linking them.

    Totals and best-round are absent by choice. Only round wins count, so a
    steady scorer can finish with the third-highest total and zero wins —
    Tit-for-Tat did exactly that in M_7444 — which makes a total actively
    misleading. The per-round row shows the same thing honestly: a flat line
    wins nothing, a spike wins a round.

    `hoard/help/hurt` is the agent's character, and it is the column that shows
    when two presets have quietly become the same one.
    """
    subs = d["submissions"]
    rounds = max(s["round"] for s in subs)
    last = max(s["turn"] for s in subs)

    score: dict[int, dict[str, int]] = defaultdict(dict)
    for s in subs:
        if s["turn"] == last:
            score[s["round"]][s["agent_id"]] = s["round_score_after"]

    wins: Counter[str] = Counter()
    for standings in score.values():
        top = max(standings.values())
        winners = [a for a, v in standings.items() if v == top]
        for a in winners:
            wins[a] += 1 / len(winners)  # a tie splits the win

    mix: dict[str, Counter[str]] = defaultdict(Counter)
    hit: Counter[str] = Counter()
    for s in subs:
        mix[s["agent_id"]][s["action"]] += 1
        if s["action"] == "HURT" and s.get("target_id"):
            hit[s["target_id"]] += 1

    header = "".join(f"{'R' + str(r):>5}" for r in range(1, rounds + 1))
    print("\n2. RESULT")
    print(f"   {'':16}{'wins':>5}  {header}{'hoard':>8}{'help':>6}{'hurt':>6}{'hit':>5}")
    for a in sorted(score[1], key=lambda x: -wins[x]):
        row = "".join(
            f"{score[r][a]:>4}*" if score[r][a] == max(score[r].values()) else f"{score[r][a]:>5}"
            for r in range(1, rounds + 1)
        )
        print(
            f"   {a:<16}{wins[a]:>5.1f}  {row}"
            f"{mix[a]['HOARD']:>8}{mix[a]['HELP']:>6}{mix[a]['HURT']:>6}{hit[a]:>5}"
        )


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    with open(sys.argv[1]) as fh:
        d = json.load(fh)
    if not d.get("submissions"):
        print(f"{sys.argv[1]}: no submissions — an unplayed match has nothing to report", file=sys.stderr)
        return 1
    print(f"MATCH {d['game']['id']} — {d['game']['name']}   rules {d['game'].get('rules_version', '?')}\n")
    validity(d)
    result(d)
    # Only the rules this match BROKE. A followed rule is the expected case, and
    # nine "100%" lines every match is how the two that matter get skipped.
    print("\n3. RULES BROKEN")
    print_fidelity(tally_rules([d]), failures_only=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
