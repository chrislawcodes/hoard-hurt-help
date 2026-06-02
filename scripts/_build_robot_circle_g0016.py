"""Transform the real G_0016 export into the robot-circle mockup's turn JSON.

Reads app/static/G_0016-data.json (talk + act log), keeps rounds 1-4, and emits
the `rc-data` shape the mockup renders: agents + turns[] with computed per-agent
net deltas (resolver rules), mutual/betrayal/missed flags, and a per-turn
badge/cap/spotlight headline.

Scoring mirrors app/engine/resolver.py; drama definitions mirror
app/engine/game_insights.py (betrayal = HURT someone you HELPed earlier in the
game; gang-up/pile-on = 2+ HURTs on one target in a turn; pact = mutual HELP).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from app.engine.rules import HOARD_POINTS, HELP_POINTS, HURT_POINTS, MUTUAL_HELP_BONUS

MAX_ROUND = 4
PILE_ON_MIN = 2

SRC = Path("app/static/G_0016-data.json")


def headline(
    rnd: int,
    turn: int,
    actions: list[dict],
    mutual_agents: set[str],
) -> tuple[str, str, list[str]]:
    """Pick the most dramatic single headline for the turn."""
    hurts = [a for a in actions if a["action"] == "HURT" and a["target"]]
    helps = [a for a in actions if a["action"] == "HELP" and a["target"]]
    missed = [a["agent"] for a in actions if a["missed"]]

    # 1. Betrayal — HURT a former ally.
    betrayals = [a for a in actions if a["betrayal"]]
    if betrayals:
        b = betrayals[0]
        spot = sorted({a["agent"] for a in betrayals} | {a["target"] for a in betrayals})
        cap = f"{b['agent']} turns on former ally {b['target']} — helped it earlier, now strikes."
        return "Betrayal", cap, spot

    # 2. Gang-up — 2+ HURTs land on one target.
    targets: dict[str, list[str]] = {}
    for a in hurts:
        targets.setdefault(a["target"], []).append(a["agent"])
    ganged = {t: atk for t, atk in targets.items() if len(atk) >= PILE_ON_MIN}
    if ganged:
        victim, attackers = max(ganged.items(), key=lambda kv: len(kv[1]))
        spot = sorted(set(attackers) | {victim})
        cap = f"{len(attackers)} agents pile on {victim}."
        return "Gang-up", cap, spot

    # 3. The Pact — a mutual-help pair (or two).
    if mutual_agents:
        spot = sorted(mutual_agents)
        if len(mutual_agents) == 2:
            a, b = spot
            cap = f"{a} and {b} lock in a mutual pact — +8 each."
        else:
            cap = "Mutual pacts lock in — +8 each."
        return "The Pact", cap, spot

    # 4. Strike — a single HURT, no prior bond.
    if hurts:
        h = hurts[0]
        spot = sorted({a["agent"] for a in hurts} | {a["target"] for a in hurts})
        cap = f"{h['agent']} strikes {h['target']}."
        return "Strike", cap, spot

    # 5. Help — a one-directional gift.
    if helps:
        spot = sorted({a["agent"] for a in helps} | {a["target"] for a in helps})
        if len(helps) == 1:
            h = helps[0]
            cap = f"{h['agent']} helps {h['target']}."
        else:
            cap = "Gifts change hands — one-way help around the circle."
        return "Help", cap, spot

    # 6. No-show — someone defaulted, otherwise quiet.
    if missed:
        spot = sorted(set(missed))
        who = missed[0]
        cap = f"{who} missed its turn — defaulted to Hoard."
        return "No-show", cap, spot

    # 7. Hoard — a quiet banking turn.
    return "Hoard", "A quiet turn — everyone banks a coin.", sorted(a["agent"] for a in actions)


def main() -> None:
    d = json.loads(SRC.read_text())
    bots: list[str] = d["bots"]
    turns: list[dict] = d["turns"]

    # Earliest HELP (round, turn) per (actor, target), across the whole log.
    earliest_help: dict[tuple[str, str], tuple[int, int]] = {}
    for t in turns:
        for a in t["act"]:
            if a["action"] == "HELP" and a.get("target"):
                key = (a["agent"], a["target"])
                when = (t["round"], t["turn"])
                if key not in earliest_help or when < earliest_help[key]:
                    earliest_help[key] = when

    out_turns: list[dict] = []
    round_score: dict[str, int] = {b: 0 for b in bots}
    cur_round: int | None = None

    for t in turns:
        if t["round"] > MAX_ROUND:
            continue
        if t["round"] != cur_round:
            cur_round = t["round"]
            round_score = {b: 0 for b in bots}  # in-round score resets each round

        act_by = {a["agent"]: a for a in t["act"]}
        talk_by = {tk["agent"]: tk for tk in t["talk"]}

        # Raw deltas (resolver rules).
        delta: dict[str, int] = {b: 0 for b in bots}
        help_targets: dict[str, str] = {}
        for b in bots:
            a = act_by.get(b)
            if not a:
                continue
            act, tgt = a["action"], a.get("target")
            if act == "HOARD":
                delta[b] += HOARD_POINTS
            elif act == "HELP" and tgt in delta:
                delta[tgt] += HELP_POINTS
                help_targets[b] = tgt
            elif act == "HURT" and tgt in delta:
                delta[tgt] -= HURT_POINTS

        # Mutual-help bonus, once per pair.
        mutual_agents: set[str] = set()
        seen: set[frozenset[str]] = set()
        for a, b in help_targets.items():
            if help_targets.get(b) == a:
                mutual_agents.update((a, b))
                pair = frozenset((a, b))
                if pair not in seen:
                    delta[a] += MUTUAL_HELP_BONUS
                    delta[b] += MUTUAL_HELP_BONUS
                    seen.add(pair)

        # Floor against the running round score; net is what the player gained.
        net: dict[str, int] = {}
        for b in bots:
            new = max(0, round_score[b] + delta[b])
            net[b] = new - round_score[b]
            round_score[b] = new

        actions: list[dict] = []
        for b in bots:
            a = act_by.get(b)
            if not a:
                continue
            act, tgt = a["action"], a.get("target")
            is_betrayal = False
            if act == "HURT" and tgt:
                fh = earliest_help.get((b, tgt))
                if fh is not None and fh < (t["round"], t["turn"]):
                    is_betrayal = True
            actions.append({
                "agent": b,
                "action": act,
                "target": tgt,
                "delta": net[b],
                "mutual": act == "HELP" and b in mutual_agents,
                "betrayal": is_betrayal,
                "missed": bool(a.get("missed")),
                "msg": (talk_by.get(b, {}).get("text") or "").strip(),
            })

        badge, cap, _ = headline(t["round"], t["turn"], actions, mutual_agents)
        # Spotlight: every bot that acts or is the recipient of an action.
        spot_set: set[str] = set()
        for a in actions:
            spot_set.add(a["agent"])
            if a.get("target"):
                spot_set.add(a["target"])
        spotlight = sorted(spot_set)
        out_turns.append({
            "round": t["round"],
            "turn": t["turn"],
            "badge": badge,
            "cap": cap,
            "spotlight": spotlight,
            "actions": actions,
        })

    payload = {
        "agents": bots,
        "turns": out_turns,
        # The export only reached round 3; report the actual highest round present
        # rather than the requested cap, so the metadata can't overstate coverage.
        "max_round": max((ot["round"] for ot in out_turns), default=0),
        "sample": False,
    }

    out_path = Path("app/static/_rc-g0016-payload.json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False))

    # Human-readable summary for verification.
    print(f"agents: {bots}")
    print(f"turns emitted: {len(out_turns)}")
    for ot in out_turns:
        deltas = " ".join(f"{a['agent']}{a['delta']:+d}" for a in ot["actions"])
        print(f"  R{ot['round']}T{ot['turn']:<2} [{ot['badge']:<8}] {ot['cap']}")
        print(f"        {deltas}")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
