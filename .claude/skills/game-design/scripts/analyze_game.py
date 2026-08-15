#!/usr/bin/env python3
"""Pull a played Hoard-Hurt-Help game and print design metrics across five
dimensions: drama, balance, comeback viability, player-count scaling, and bad
game states.

This is the mechanical first step of a game-design review. It turns the public
spectator JSON into the evidence table you reason from before proposing any fix.

Stdlib only (urllib + json), so it runs anywhere — no venv needed.

Usage:
    python scripts/analyze_game.py G_0017
    python scripts/analyze_game.py G_0017 --base http://localhost:8766
    python scripts/analyze_game.py --file /tmp/game.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter, defaultdict

DEAD_ACTION_THRESHOLD = 0.02   # action used <2% of turns is effectively dead
LOCK_THRESHOLD = 0.50          # reciprocal pair firing >50% of turns is "locked"
# Max per-turn gap closure: best possible for trailer minus safest for leader
# (+2 hoard). Used for comeback feasibility. The mutual-help payout varies by
# match (MutualHelpMode in app/games/hoard_hurt_help/rules.py); this assumes
# today's platform default, flat_6 (+6 mutual - +2 hoard = 4). A decay/
# no_repeats/flat_7/flat_8 match's real ceiling is higher (up to +8 on a fresh
# pact), so this constant undercounts comeback room for those matches.
MAX_CATCHUP_PER_TURN = 4


def _load(game_id: str | None, base: str, path: str | None) -> dict:
    if path:
        with open(path) as f:
            return json.load(f)
    url = f"{base.rstrip('/')}/api/spectator/games/{game_id}/state"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return json.load(resp)
    except Exception as e:
        print(f"  fetch failed ({e}); try --file /tmp/game.json after:")
        print(f"  curl -s '{url}' -o /tmp/game.json")
        sys.exit(1)


def _action_mix(history: list[dict]) -> tuple[Counter, dict[str, Counter]]:
    overall: Counter = Counter()
    per_agent: dict[str, Counter] = defaultdict(Counter)
    for turn in history:
        for a in turn["actions"]:
            overall[a["action"]] += 1
            per_agent[a["agent_id"]][a["action"]] += 1
    return overall, per_agent


def _help_pairs(history: list[dict]) -> Counter:
    pairs: Counter = Counter()
    for turn in history:
        for a in turn["actions"]:
            if a["action"] == "HELP" and a.get("target_id"):
                pairs[(a["agent_id"], a["target_id"])] += 1
    return pairs


def _hurt_events(history: list[dict]) -> list[tuple]:
    out = []
    for turn in history:
        for a in turn["actions"]:
            if a["action"] == "HURT":
                out.append((turn["round"], turn["turn"], a["agent_id"], a.get("target_id")))
    return out


def _locked_pairs(pairs: Counter, total_turns: int) -> list[tuple[str, str, int, int]]:
    """Reciprocal HELP pairs where both directions exceed the lock threshold."""
    locked = []
    seen: set[frozenset[str]] = set()
    for (src, dst), n in pairs.items():
        back = pairs.get((dst, src), 0)
        key = frozenset({src, dst})
        if key in seen:
            continue
        if n >= total_turns * LOCK_THRESHOLD and back >= total_turns * LOCK_THRESHOLD:
            locked.append((src, dst, n, back))
            seen.add(key)
    return sorted(locked, key=lambda x: -(x[2] + x[3]))


def _round_breakdown(history: list[dict]) -> dict[int, dict[str, int]]:
    rounds: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for turn in history:
        for a in turn["actions"]:
            rounds[turn["round"]][a["agent_id"]] += a["points_delta"]
    return rounds


def _round_settle_turn(history: list[dict], round_num: int) -> tuple[int, int]:
    """Last turn the lead changed hands. Low value = winner locked early."""
    turns = sorted([t for t in history if t["round"] == round_num], key=lambda t: t["turn"])
    running: dict[str, int] = defaultdict(int)
    prev_leader: str | None = None
    last_change = 0
    for t in turns:
        for a in t["actions"]:
            running[a["agent_id"]] += a["points_delta"]
        leader = max(running, key=lambda k: running[k]) if running else None
        if leader != prev_leader:
            last_change = t["turn"]
            prev_leader = leader
    return last_change, (turns[-1]["turn"] if turns else 0)


def _comeback_feasibility(
    history: list[dict], rounds: dict[int, dict[str, int]], turns_per_round: int
) -> list[dict]:
    """For each round, at the midpoint turn check whether the lowest scorer
    could mathematically catch up to the leader in the remaining turns.

    Returns a list of dicts with round, midpoint gap, remaining turns,
    max_catchup, and whether a comeback was possible."""
    results = []
    mid = turns_per_round // 2
    for r in sorted(rounds):
        # Rebuild running scores at midpoint
        running: dict[str, int] = defaultdict(int)
        for t in sorted([t for t in history if t["round"] == r], key=lambda t: t["turn"]):
            if t["turn"] > mid:
                break
            for a in t["actions"]:
                running[a["agent_id"]] += a["points_delta"]
        if not running:
            continue
        high = max(running.values())
        low = min(running.values())
        gap = high - low
        remaining = turns_per_round - mid
        max_catchup = MAX_CATCHUP_PER_TURN * remaining
        possible = gap <= max_catchup
        results.append({
            "round": r,
            "gap_at_mid": gap,
            "remaining_turns": remaining,
            "max_catchup": max_catchup,
            "possible": possible,
        })
    return results


def _dominant_agent(rounds: dict[int, dict[str, int]]) -> dict[str, int]:
    """Count how many rounds each agent won outright (sole leader)."""
    wins: dict[str, int] = Counter()
    for pts in rounds.values():
        top = max(pts.values())
        sole_winners = [a for a, v in pts.items() if v == top]
        if len(sole_winners) == 1:
            wins[sole_winners[0]] += 1
    return dict(wins)


def _match_clinched(scoreboard: list[dict], n_rounds: int) -> str | None:
    """Check if the match winner could be identified before the final round.
    Returns a description string if clinched early, else None."""
    # Check if the leader's wins exceed what 2nd place could reach
    rows = sorted(scoreboard, key=lambda r: -r["round_wins"])
    if len(rows) < 2:
        return None
    leader_wins = rows[0]["round_wins"]
    second_wins = rows[1]["round_wins"]
    # Check if leader - second > remaining rounds, meaning it was clinched
    # We don't have per-round win history here, so flag if final gap > 1.5
    gap = leader_wins - second_wins
    if gap > n_rounds * 0.3:
        return f"{rows[0]['agent_id']} clinched with {leader_wins:.2f} wins vs {second_wins:.2f} (gap {gap:.2f} > 30% of rounds)"
    return None


def _bad_states(
    rounds: dict[int, dict[str, int]],
    comeback_data: list[dict],
    turns_per_round: int,
) -> list[str]:
    """Detect conditions that make a game feel broken."""
    flags = []

    # Ceiling lock: N-way tie where everyone hit the same score
    ceiling_ties = [(r, pts) for r, pts in rounds.items()
                    if len(set(pts.values())) == 1 and len(pts) > 2]
    if ceiling_ties:
        flags.append(
            f"CEILING LOCK — {len(ceiling_ties)} round(s) where ALL players tied at"
            f" {list(ceiling_ties[0][1].values())[0]}:"
            f" R{','.join(str(r) for r, _ in ceiling_ties)}"
        )

    # Mathematical impossibility: rounds where comeback was impossible at midpoint
    impossible = [c for c in comeback_data if not c["possible"]]
    if impossible:
        flags.append(
            f"COMEBACK IMPOSSIBLE mid-round in {len(impossible)}/{len(comeback_data)} rounds "
            f"(gap > max catchup in {impossible[0]['remaining_turns']} turns)"
        )

    # Zero-floor hits: any agent ended a round at 0 pts
    zero_finishers = [(r, a) for r, pts in rounds.items() for a, v in pts.items() if v == 0]
    if zero_finishers:
        flags.append(f"SCORE FLOOR HIT — {len(zero_finishers)} agent-round(s) ended at 0 pts")

    return flags


def _round_settle_turn_static(rounds: dict, r: int) -> int:
    """Simplified settle-turn approximation for bad-state detection."""
    pts = rounds.get(r, {})
    top = max(pts.values()) if pts else 0
    winners = [a for a, v in pts.items() if v == top]
    return 1 if len(winners) == 1 else 0


def _bar(frac: float, width: int = 24) -> str:
    filled = round(frac * width)
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("game_id", nargs="?", help="e.g. G_0017")
    ap.add_argument("--base", default="https://agentludum.com", help="server base URL")
    ap.add_argument("--file", help="read saved spectator JSON instead of fetching")
    args = ap.parse_args()
    if not args.game_id and not args.file:
        ap.error("give a game_id or --file")

    d = _load(args.game_id, args.base, args.file)
    history = d.get("history", [])
    if not history:
        print("No resolved turns in this game yet — nothing to analyze.")
        return 1

    total_turns = len({(t["round"], t["turn"]) for t in history})
    n_rounds = max(t["round"] for t in history)
    turns_per_round = max(t["turn"] for t in history)
    agents = d.get("agents", [])
    n_players = len(agents)

    print(f"\n=== {d.get('name', '?')}  ({args.game_id or args.file}) ===")
    print(f"state: {d.get('state')}   {n_rounds} rounds x {turns_per_round} turns   "
          f"({total_turns} turns resolved)   {n_players} players")
    print("\nagents:")
    for a in agents:
        print(f"  {a['agent_id']:<10} {a.get('model_self_report', '')}")

    scoreboard = d.get("scoreboard", [])
    print("\nfinal standings:")
    for row in sorted(scoreboard, key=lambda r: -r["round_wins"]):
        print(f"  {row['agent_id']:<10} {row['round_wins']:>5} wins   "
              f"{row['round_score']:>4} pts (final round)")

    # ── ACTION MIX ──────────────────────────────────────────────────────────
    overall, per_agent = _action_mix(history)
    total_actions = sum(overall.values())
    print(f"\n--- ACTION MIX ({total_actions} actions) ---")
    dead = []
    for act in ("HOARD", "HELP", "HURT"):
        n = overall.get(act, 0)
        frac = n / total_actions if total_actions else 0
        flag = "  <-- DEAD ACTION" if frac < DEAD_ACTION_THRESHOLD else ""
        if frac < DEAD_ACTION_THRESHOLD:
            dead.append(act)
        print(f"  {act:<6} {_bar(frac)} {n:>4} ({frac*100:>4.0f}%){flag}")
    print("\nper agent:")
    for ag, c in per_agent.items():
        print(f"  {ag:<10} " + ", ".join(f"{k}={v}" for k, v in c.most_common()))

    # ── ALLIANCES ───────────────────────────────────────────────────────────
    pairs = _help_pairs(history)
    locked = _locked_pairs(pairs, total_turns)
    print("\n--- ALLIANCES (reciprocal HELP locks) ---")
    if locked:
        for src, dst, n, back in locked:
            print(f"  {src} <-> {dst}:  {src}->{dst} {n}x,  {dst}->{src} {back}x   LOCKED")
    else:
        print("  no locked pair — alliances shifted (good)")

    # ── AGGRESSION ──────────────────────────────────────────────────────────
    hurts = _hurt_events(history)
    print(f"\n--- AGGRESSION ---\n  HURT used {len(hurts)} times total")
    for r, t, src, dst in hurts[:12]:
        print(f"    R{r} t{t}: {src} -> {dst}")
    if len(hurts) > 12:
        print(f"    ... and {len(hurts) - 12} more")

    # ── PER-ROUND RESULT ────────────────────────────────────────────────────
    rounds = _round_breakdown(history)
    print("\n--- PER-ROUND RESULT ---")
    tie_rounds = []
    settle_turns = []
    for r in sorted(rounds):
        pts = rounds[r]
        top = max(pts.values())
        winners = [a for a, v in pts.items() if v == top]
        settle, mx = _round_settle_turn(history, r)
        settle_turns.append(settle)
        scoreline = ", ".join(f"{a}={v}" for a, v in sorted(pts.items(), key=lambda x: -x[1]))
        if len(winners) > 1:
            tie_rounds.append((r, len(winners)))
            verdict = f"{len(winners)}-WAY TIE"
        else:
            verdict = f"won by {winners[0]}"
        print(f"  R{r:>2}: {scoreline}  -> {verdict}  (settled t{settle}/{mx})")

    avg_settle = sum(settle_turns) / len(settle_turns) if settle_turns else 0

    # ── BALANCE: DOMINANT AGENT ─────────────────────────────────────────────
    sole_wins = _dominant_agent(rounds)
    print("\n--- BALANCE (sole-round wins) ---")
    for ag, w in sorted(sole_wins.items(), key=lambda x: -x[1]):
        print(f"  {ag:<10} {w} outright wins")
    if sole_wins:
        top_agent = max(sole_wins, key=sole_wins.get)
        top_w = sole_wins[top_agent]
        if top_w > n_rounds * 0.5:
            print(f"  *** {top_agent} won >50% of rounds outright — possible dominant strategy")

    # ── COMEBACK FEASIBILITY ────────────────────────────────────────────────
    comeback = _comeback_feasibility(history, rounds, turns_per_round)
    impossible_count = sum(1 for c in comeback if not c["possible"])
    print(f"\n--- COMEBACK FEASIBILITY (at round midpoint, max catchup = {MAX_CATCHUP_PER_TURN} pts/turn) ---")
    for c in comeback:
        status = "possible" if c["possible"] else "IMPOSSIBLE"
        print(f"  R{c['round']:>2}: gap={c['gap_at_mid']:>3}  max_catchup={c['max_catchup']}  -> {status}")
    print(f"  Summary: comeback impossible in {impossible_count}/{len(comeback)} rounds")

    # ── PLAYER COUNT NOTE ───────────────────────────────────────────────────
    print(f"\n--- PLAYER COUNT ({n_players} players) ---")
    pairs_possible = n_players // 2
    print(f"  {pairs_possible} mutual-HELP pairs can form simultaneously")
    if n_players <= 4:
        print("  At 4 players: 2 pairs fit exactly → both can hit the score ceiling → ties")
        print("  At 8–12: more competition for HELP targets, HURT becomes more relevant")
        print("  At 15–20: natural scarcity emerges — not everyone can pair up")
    else:
        print(f"  At {n_players} players: pairing competition is real; HURT has more strategic targets")

    # ── BAD STATES ──────────────────────────────────────────────────────────
    bad = _bad_states(rounds, comeback, turns_per_round)
    clinched = _match_clinched(scoreboard, n_rounds)
    if clinched:
        bad.append(f"MATCH CLINCHED EARLY: {clinched}")
    print("\n--- BAD STATES ---")
    if bad:
        for b in bad:
            print(f"  !! {b}")
    else:
        print("  none detected")

    # ── SUMMARY ─────────────────────────────────────────────────────────────
    print("\n=== DESIGN SIGNALS SUMMARY ===")
    print(f"  tie rounds:           {len(tie_rounds)} / {n_rounds}")
    print(f"  dead actions:         {dead or 'none'}")
    print(f"  locked alliances:     {len(locked)}"
          + (f"  ({', '.join(f'{a}<->{b}' for a, b, _, _ in locked)})" if locked else ""))
    print(f"  avg settle turn:      {avg_settle:.1f} / {turns_per_round}  (low = foregone)")
    print(f"  comeback impossible:  {impossible_count} / {len(comeback)} rounds")
    print(f"  bad states:           {len(bad)}")
    print()
    flags = [len(tie_rounds) > n_rounds // 3, bool(dead), bool(locked),
             avg_settle < turns_per_round * 0.6, impossible_count > len(comeback) // 2]
    if sum(flags) >= 2:
        print("  READ: multiple design problems detected — ground proposals in this data.")
        print("  Cross-reference references/boardgame-design-patterns.md for prior art.")
    else:
        print("  READ: shifting leads, live aggression, decisive rounds — relatively healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
