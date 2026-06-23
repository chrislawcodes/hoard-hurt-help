"""End-of-game finale data for a completed match.

A pure, DB-free builder that turns the already-loaded scoreboard + replay history
into the data the final scoreboard renders: the champion, the standings ordered by
the metric that actually decides the match (rounds won, then points as a
tiebreaker), each seat's Hoard/Help/Hurt mix, and a short list of match
superlatives ("biggest turn", "tightest pact", …) that only appear when there is
real signal to show.

Kept free of the database — a pure function over plain data — so it is trivially
unit-testable, matching the style of `opponent_stats` and `turn_summary`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _fmt_wins(wins: float) -> str:
    """Round-wins for display: whole numbers as integers, tie-split fractions to
    at most two decimals ("2", "0.5", "0.67") — never a raw float like 0.666667.
    """
    if wins == int(wins):
        return str(int(wins))
    return f"{wins:.2f}".rstrip("0").rstrip(".")


def _action_mix(history: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per-seat Hoard/Help/Hurt split as whole percentages that sum to 100.

    A seat that never acted maps to all-zero (an empty bar), not a missing key.
    """
    counts: dict[str, dict[str, int]] = {}
    for turn in history:
        for action in turn.get("actions", []):
            seat = action["agent_id"]
            bucket = counts.setdefault(seat, {"hoard": 0, "help": 0, "hurt": 0})
            kind = action["action"]
            if kind == "HELP":
                bucket["help"] += 1
            elif kind == "HURT":
                bucket["hurt"] += 1
            else:
                bucket["hoard"] += 1

    mix: dict[str, dict[str, int]] = {}
    for seat, bucket in counts.items():
        total = bucket["hoard"] + bucket["help"] + bucket["hurt"]
        if total == 0:
            mix[seat] = {"hoard": 0, "help": 0, "hurt": 0}
            continue
        hoard = round(100 * bucket["hoard"] / total)
        help_ = round(100 * bucket["help"] / total)
        # Derive the last slice so the three always sum to exactly 100.
        hurt = max(0, 100 - hoard - help_)
        mix[seat] = {"hoard": hoard, "help": help_, "hurt": hurt}
    return mix


def _superlatives(
    history: list[dict[str, Any]], name_of: dict[str, str]
) -> tuple[list[dict[str, str]], bool]:
    """Match highlights with real signal, plus a 'quiet match' flag.

    Returns (stats, quiet). ``quiet`` is True when nobody helped, hurt, or formed
    a pact — an all-Hoard match where there is nothing dramatic to report.
    """
    helps: Counter[str] = Counter()
    hurts: Counter[str] = Counter()
    pacts: Counter[frozenset[str]] = Counter()
    biggest: tuple[int, str, int, int] | None = None  # (delta, seat, round, turn)

    for turn in history:
        seen_pairs: set[frozenset[str]] = set()
        for action in turn.get("actions", []):
            seat = action["agent_id"]
            kind = action["action"]
            if kind == "HELP":
                helps[seat] += 1
            elif kind == "HURT":
                hurts[seat] += 1

            delta = action.get("display_delta") or 0
            if delta > 0 and (biggest is None or delta > biggest[0]):
                biggest = (delta, seat, turn["round"], turn["turn"])

            target = action.get("target_id")
            if action.get("mutual") and target:
                pair = frozenset((seat, target))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    pacts[pair] += 1

    def _top(counter: Counter[str]) -> tuple[str, int] | None:
        if not counter:
            return None
        # Deterministic: highest count, then the alphabetically first name.
        seat = min(counter, key=lambda s: (-counter[s], name_of.get(s, s)))
        return seat, counter[seat]

    stats: list[dict[str, str]] = []

    # Biggest single swing, but only when it beats a plain Hoard (+2) — otherwise
    # it is not a highlight, it is the floor.
    if biggest is not None and biggest[0] > 2:
        delta, seat, rnd, turn_no = biggest
        stats.append(
            {
                "kind": "hoard",
                "label": "Biggest turn",
                "value": f"{name_of.get(seat, seat)} +{delta} · R{rnd} T{turn_no}",
            }
        )

    if pacts:
        pair, n = max(pacts.items(), key=lambda kv: (kv[1], -1))
        a, b = sorted(pair)
        stats.append(
            {
                "kind": "help",
                "label": "Tightest pact",
                "value": f"{name_of.get(a, a)} ↔ {name_of.get(b, b)} · {n}×",
            }
        )

    ruthless = _top(hurts)
    if ruthless is not None:
        seat, n = ruthless
        stats.append(
            {
                "kind": "hurt",
                "label": "Most ruthless",
                "value": f"{name_of.get(seat, seat)} · {n} hurt{'s' if n != 1 else ''}",
            }
        )

    generous = _top(helps)
    if generous is not None:
        seat, n = generous
        stats.append(
            {
                "kind": "help",
                "label": "Most generous",
                "value": f"{name_of.get(seat, seat)} · {n} help{'s' if n != 1 else ''}",
            }
        )

    quiet = not helps and not hurts and not pacts
    return stats, quiet


def build_final_summary(
    *,
    total_rounds: int,
    scoreboard: list[dict[str, Any]],
    total_scores: dict[str, int],
    history: list[dict[str, Any]],
    winner_seat: str | None,
) -> dict[str, Any] | None:
    """Assemble the finale payload for a completed match.

    ``scoreboard`` rows carry the public labels (display name, owner, provider,
    bot flag) and ``round_wins`` (the seat's total round wins). ``total_scores``
    maps seat → total points across all rounds, the match tiebreaker. Returns
    None when there are no players to rank.
    """
    if not scoreboard:
        return None

    name_of = {row["agent_id"]: row["display_name"] for row in scoreboard}
    mix = _action_mix(history)

    standings: list[dict[str, Any]] = []
    for row in scoreboard:
        seat = row["agent_id"]
        standings.append(
            {
                "agent_id": seat,
                "display_name": row["display_name"],
                "round_wins": row["round_wins"],
                "round_wins_label": _fmt_wins(row["round_wins"]),
                "total_score": total_scores.get(seat, 0),
                "is_bot": row.get("is_bot", False),
                "owner_handle": row.get("owner_handle"),
                "provider": row.get("provider"),
                "mix": mix.get(seat, {"hoard": 0, "help": 0, "hurt": 0}),
            }
        )
    # Rounds won is the score; points break ties; name keeps it deterministic.
    standings.sort(key=lambda r: (-r["round_wins"], -r["total_score"], r["display_name"]))
    for i, row in enumerate(standings, start=1):
        row["rank"] = i

    # Champion is the engine's recorded winner; fall back to the top of the
    # rule-sorted standings (they should agree).
    champion = next((r for r in standings if r["agent_id"] == winner_seat), None)
    if champion is None:
        champion = standings[0]

    # The champion won "on points" when someone else tied them on rounds won.
    decided_by_points = (
        sum(1 for r in standings if r["round_wins"] == champion["round_wins"]) > 1
    )

    # Where to draw the "no rounds won — ordered by points" divider: the first
    # seat with zero round wins, but only if a winner sits above it.
    first_zero_rank = next(
        (r["rank"] for r in standings if r["round_wins"] == 0 and r["rank"] > 1),
        None,
    )

    stats, quiet = _superlatives(history, name_of)

    return {
        "champion": champion,
        "champion_decided_by_points": decided_by_points,
        "total_rounds": total_rounds,
        "standings": standings,
        "first_zero_rank": first_zero_rank,
        "stats": stats,
        "quiet": quiet,
    }
