"""Presentation helpers for the match viewer and replay data."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.games import get as get_game_module
from app.games.base import GameError

# A real, recorded match (G_0016) bundled in the same robot-circle JSON format
# `_build_rc_data` emits. It seeds the homepage/lobby replay so the animation
# always plays, even before a live showcase game exists.
_SAMPLE_REPLAY_PATH = Path(__file__).resolve().parent.parent / "static" / "_rc-g0016-payload.json"


@lru_cache(maxsize=1)
def sample_replay_data() -> str:
    """Robot-circle replay JSON for the bundled sample match.

    Used as the homepage/lobby fallback so the animated replay is never a dead
    placeholder. Marked ``sample`` so callers (and any future UI) can tell it
    apart from a real, just-played game. Read once, then cached.
    """
    payload = json.loads(_SAMPLE_REPLAY_PATH.read_text(encoding="utf-8"))
    payload["sample"] = True
    payload.setdefault("owners", {})
    return json.dumps(payload, ensure_ascii=False)


def _move_effect_for(game_type: str, action: str) -> tuple[int, int | None]:
    """Nominal per-move effect for the watch feed, split into (actor_delta, target_delta).

    Delegates to the game module so the viewer carries no game-specific scoring.
    This is what the move is worth by that game's rules, shown per-move so
    viewers see who each move lands on. It is deliberately not the player's net
    change for the turn, which folds in others' moves, bonuses, and the floor.
    """
    try:
        return get_game_module(game_type).move_effect(action)
    except GameError:
        return 0, None


def _feed_sort_key(a: dict) -> tuple[int, int, str]:
    """Highlights-first ordering for one turn's actions in the feed."""
    if a.get("betrayal"):
        tier = 0
    elif a.get("mutual"):
        tier = 1
    elif a.get("was_defaulted"):
        tier = 5
    elif a["action"] == "HURT":
        tier = 2
    elif a["action"] == "HELP":
        tier = 3
    else:
        tier = 4
    delta = a.get("display_delta") or 0
    return (tier, -abs(delta), a["agent_id"])


def _turn_summary(actions: list[dict]) -> dict[str, int]:
    """Per-turn action counts for the feed's at-a-glance summary line."""
    counts = {"help": 0, "hurt": 0, "hoard": 0, "betrayal": 0, "mutual": 0, "missed": 0}
    for a in actions:
        act = a["action"].lower()
        if act in ("help", "hurt", "hoard"):
            counts[act] += 1
        if a.get("betrayal"):
            counts["betrayal"] += 1
        if a.get("mutual"):
            counts["mutual"] += 1
        if a.get("was_defaulted"):
            counts["missed"] += 1
    return counts


def _turn_groups(actions: list[dict]) -> list[dict]:
    """Group a turn's actions by type for the Compact view."""
    hurts: list[dict] = []
    helps: list[dict] = []
    hoards: list[dict] = []
    pacts: list[dict] = []
    seen_pacts: set[frozenset[str]] = set()
    for a in actions:
        if a.get("mutual") and a["target_id"]:
            pair = frozenset((a["agent_id"], a["target_id"]))
            if pair not in seen_pacts:
                seen_pacts.add(pair)
                x, y = sorted(pair)
                pacts.append({"a": x, "b": y})
        elif a["action"] == "HURT" and a["target_id"]:
            hurts.append(
                {
                    "a": a["agent_id"],
                    "b": a["target_id"],
                    "betrayal": bool(a.get("betrayal")),
                }
            )
        elif a["action"] == "HELP" and a["target_id"]:
            helps.append({"a": a["agent_id"], "b": a["target_id"]})
        else:
            hoards.append({"a": a["agent_id"]})

    hurts.sort(key=lambda h: (not h["betrayal"], h["a"]))

    groups: list[dict] = []
    if hurts:
        groups.append({"kind": "hurt", "delta": "-4", "members": hurts})
    if pacts:
        groups.append({"kind": "pact", "delta": "+8", "members": pacts})
    if helps:
        groups.append({"kind": "help", "delta": "+4", "members": helps})
    if hoards:
        groups.append({"kind": "hoard", "delta": "+2", "members": hoards})
    return groups


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


def _build_rc_data(scoreboard: list[dict], history: list[dict]) -> str:
    """Serialize game history as the robot-circle viewer JSON format."""
    agents = [r["agent_id"] for r in scoreboard]
    # agent_id → owner handle, for the standings rail's muted "by @handle" line.
    # Only non-empty entries (Sims and handle-less owners are omitted).
    owners = {r["agent_id"]: r["owner_handle"] for r in scoreboard if r.get("owner_handle")}

    turns = []
    for h in history:
        rc_actions = []
        for a in h["actions"]:
            rc_actions.append(
                {
                    "agent": a["agent_id"],
                    "action": a["action"],
                    "target": a["target_id"],
                    "delta": a["display_delta"],
                    "mutual": a["mutual"],
                    "betrayal": a["betrayal"],
                    "missed": a["was_defaulted"],
                    "msg": (a.get("message") or "").strip(),
                }
            )

        spot: set[str] = set()
        for a in rc_actions:
            spot.add(a["agent"])
            if a["target"]:
                spot.add(a["target"])

        betrayals = [a for a in rc_actions if a["betrayal"]]
        mutuals = [a for a in rc_actions if a["mutual"]]
        hurts = [a for a in rc_actions if a["action"] == "HURT" and a["target"]]
        helps = [
            a
            for a in rc_actions
            if a["action"] == "HELP" and not a["mutual"] and a["target"]
        ]
        missed = [a for a in rc_actions if a["missed"]]

        if betrayals:
            b = betrayals[0]
            badge, cap = "Betrayal", f"{b['agent']} turns on former ally {b['target']}."
        elif mutuals:
            pair = sorted({a["agent"] for a in mutuals} | {a["target"] for a in mutuals})
            if len(pair) == 2:
                badge, cap = (
                    "The Pact",
                    f"{pair[0]} and {pair[1]} lock in a mutual pact — +8 each.",
                )
            else:
                badge, cap = "The Pact", "Mutual pacts lock in — +8 each."
        elif hurts:
            h0 = hurts[0]
            badge, cap = "Strike", f"{h0['agent']} strikes {h0['target']}."
        elif helps:
            badge = "Help"
            cap = (
                f"{helps[0]['agent']} helps {helps[0]['target']}."
                if len(helps) == 1
                else "Gifts change hands — one-way help around the circle."
            )
        elif missed and len(missed) == len(rc_actions):
            badge, cap = "No-show", f"{missed[0]['agent']} missed its turn — defaulted to Hoard."
        else:
            badge, cap = "Hoard", "A quiet turn — everyone banks a coin."

        talk = [
            {"agent": m["agent_id"], "text": m["text"].strip()}
            for m in h["messages"]
            if m["text"].strip()
        ]

        turns.append(
            {
                "round": h["round"],
                "turn": h["turn"],
                "badge": badge,
                "cap": cap,
                "spotlight": sorted(spot),
                "actions": rc_actions,
                "talk": talk,
            }
        )

    return json.dumps(
        {
            "agents": agents,
            "owners": owners,
            "turns": turns,
            "max_round": max((t["round"] for t in turns), default=0),
            "sample": False,
        },
        ensure_ascii=False,
    )
