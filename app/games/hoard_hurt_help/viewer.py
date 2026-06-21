"""PD-specific viewer presentation: the replay "story".

The platform viewer route loads the generic skeleton (players, scoreboard,
timeline, messages, rounds) and asks each game module to build its own display
payload via `build_replay_view`. This file is PD's payload builder: the per-turn
pact/betrayal tagging, the deterministic play-by-play headline, the feed
ordering/summary/grouping, and the robot-circle replay JSON.

These were the PD-specific parts of the platform's old `_game_view_context` and
`app/engine/viewer_presentation.py`; they are moved here verbatim so the
platform route carries no game-specific scoring or narrative.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.games.hoard_hurt_help.scoring import apply_inround_turn
from app.games.viewer_common import (
    project_turn_messages,
    rc_envelope,
    rc_scoreboard_maps,
    rc_talk,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.match import Match
    from app.models.player import Player
    from app.read_models.matches import TimelineTurn

# Calibration ±pp per round (measured on held-out test set via permutation
# of predicted vs actual within calibration buckets). Round 1 excluded —
# all ten players sit at ~10%, ranges add no signal.
_ROUND_CAL_MAE: dict[int, float] = {
    2: 0.06, 3: 0.06, 4: 0.05, 5: 0.08, 6: 0.07, 7: 0.05,
}

# A real, recorded match (G_0016) bundled in the same robot-circle JSON format
# `_build_rc_data` emits. It seeds the homepage/lobby replay so the animation
# always plays, even before a live showcase game exists.
_SAMPLE_REPLAY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "static" / "_rc-g0016-payload.json"
)


@lru_cache(maxsize=1)
def sample_replay_data() -> str:
    """Robot-circle replay JSON for the bundled sample match.

    Used as the homepage/lobby fallback so the animated replay is never a dead
    placeholder. Marked ``sample`` so callers (and any future UI) can tell it
    apart from a real, just-played game. Read once, then cached.
    """
    payload = json.loads(_SAMPLE_REPLAY_PATH.read_text(encoding="utf-8"))
    payload["sample"] = True
    payload.setdefault("labels", {agent_id: agent_id for agent_id in payload.get("agents", [])})
    payload.setdefault("bots", {})
    payload.setdefault("owners", {})
    payload.setdefault("providers", {})
    return json.dumps(payload, ensure_ascii=False)


def _move_effect_for(game_type: str, action: str) -> tuple[int, int | None]:
    """Nominal per-move effect for the watch feed, split into (actor_delta, target_delta).

    Delegates to the game module so the viewer carries no game-specific scoring.
    This is what the move is worth by that game's rules, shown per-move so
    viewers see who each move lands on. It is deliberately not the player's net
    change for the turn, which folds in others' moves, bonuses, and the floor.
    """
    from app.games import get as get_game_module
    from app.games.base import GameError

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


def _compute_round_win_probs(
    scoreboard: list[dict[str, Any]],
    history: list[dict[str, Any]],
    turns_per_round: int,
) -> dict[tuple[int, int], dict[str, dict[str, float]]]:
    """Return {(round, turn): {agent_id: {p, lo, hi}}} for every completed turn.

    Converts the viewer history into engine records, runs score_round_win() at
    each turn boundary, and attaches calibration bands.  Returns {} silently if
    the model file is absent.
    """
    from app.engine.game_records import ActionRecord, PlayerRecord
    from app.engine.win_probability import score_round_win

    agents = [r["agent_id"] for r in scoreboard]
    if not agents or not history:
        return {}

    # Last turn number per round — needed to detect round completion.
    last_turn_by_round: dict[int, int] = {}
    for h in history:
        rnd = h["round"]
        last_turn_by_round[rnd] = max(last_turn_by_round.get(rnd, 0), h["turn"])

    inround: dict[str, int] = {a: 0 for a in agents}
    round_wins: dict[str, float] = {a: 0.0 for a in agents}
    current_round: int | None = None
    all_action_records: list[ActionRecord] = []
    result: dict[tuple[int, int], dict[str, dict[str, float]]] = {}

    for h in history:
        rnd, turn = h["round"], h["turn"]

        if rnd != current_round:
            current_round = rnd
            inround = {a: 0 for a in agents}

        scores_before = dict(inround)

        # Apply this turn's actions to get post-turn scores.
        new_inround = apply_inround_turn(inround, h["actions"])

        for a in h["actions"]:
            actor = a["agent_id"]
            all_action_records.append(
                ActionRecord(
                    round=rnd,
                    turn=turn,
                    actor_id=actor,
                    action=a["action"],
                    target_id=a.get("target_id"),
                    message="",
                    points_delta=new_inround.get(actor, 0) - scores_before.get(actor, 0),
                    round_score_after=new_inround.get(actor, 0),
                    was_defaulted=a.get("was_defaulted", False),
                )
            )

        inround = new_inround

        player_records = [
            PlayerRecord(
                agent_id=a,
                round_score=inround.get(a, 0),
                total_score=0,
                round_wins=round_wins[a],
            )
            for a in agents
        ]

        probs = score_round_win(player_records, all_action_records, rnd, turn, turns_per_round)
        if probs:
            cal = _ROUND_CAL_MAE.get(rnd, 0.0)
            result[(rnd, turn)] = {
                pid: {
                    "p": round(p, 3),
                    "lo": round(max(0.0, p - cal), 3),
                    "hi": round(min(1.0, p + cal), 3),
                }
                for pid, p in probs.items()
            }

        # At end of round, credit round wins for the next round's PlayerRecords.
        if turn == last_turn_by_round.get(rnd, -1):
            best = max(inround.values()) if inround else 0
            winners = [a for a in agents if inround.get(a, 0) == best]
            share = 1.0 / len(winners) if winners else 0.0
            for w in winners:
                round_wins[w] += share

    return result


def _build_rc_data(
    scoreboard: list[dict[str, Any]],
    history: list[dict[str, Any]],
    turns_per_round: int = 7,
    viewer_seat: str | None = None,
) -> str:
    """Serialize game history as the robot-circle viewer JSON format."""
    agents, labels, bots, owners = rc_scoreboard_maps(scoreboard)
    # agent_id → provider label (Claude/Gemini/…) that actually played the seat,
    # for the standings rail's per-competitor badge. Omitted for bots and seats
    # not yet served (no provider). PD-only enrichment on top of the shared maps.
    providers = {r["agent_id"]: r["provider"] for r in scoreboard if r.get("provider")}

    win_probs_by_turn = _compute_round_win_probs(scoreboard, history, turns_per_round)

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

        turns.append(
            {
                "round": h["round"],
                "turn": h["turn"],
                "badge": badge,
                "cap": cap,
                "spotlight": sorted(spot),
                "actions": rc_actions,
                "talk": rc_talk(h),
                "win_probs": win_probs_by_turn.get((h["round"], h["turn"]), {}),
            }
        )

    return rc_envelope(
        agents=agents,
        labels=labels,
        bots=bots,
        owners=owners,
        turns=turns,
        viewer_seat=viewer_seat,
        # PD enriches the shared envelope with a per-seat provider badge map;
        # it slots in right after `owners`, matching PD's historical key order.
        extra_maps={"providers": providers},
    )


async def build_pd_replay_view(
    db: AsyncSession,
    match: Match,
    players: list[Player],
    scoreboard: list[dict[str, Any]],
    timeline: list[TimelineTurn],
    viewer_seat: str | None,
) -> dict[str, Any]:
    """Build PD's display payload: the enriched ``history`` and the ``rc_data`` JSON.

    Moved verbatim from the platform's old ``_game_view_context``: it tags each
    turn's pacts (mutual HELP) and betrayals (HURT on last turn's pact partner),
    attaches the per-move display action/delta, tracks the in-round running score
    to pick the leader for the play-by-play headline, and emits the feed
    ordering/summary/grouping and the robot-circle replay JSON.
    """
    g = match
    history: list[dict[str, Any]] = []

    # Per-turn pact/betrayal signals for the replay. A "pact" is a mutual HELP in
    # the same turn; a "betrayal" is a HURT aimed at last turn's pact partner.
    prev_mutual: set[frozenset[str]] = set()
    # Carried across turns to narrate a deterministic play-by-play headline.
    prev_actions: list[dict[str, Any]] = []
    prev_leader: str | None = None
    inround: dict[str, int] = {}
    inround_round: int | None = None
    for seq, t in enumerate(timeline, start=1):
        messages, messages_by_agent = project_turn_messages(t)
        actions: list[dict[str, Any]] = []
        for action in t.actions:
            actor_delta, target_delta = _move_effect_for(g.game, action.action)
            actions.append(
                {
                    "agent_id": action.agent_id,
                    "action": action.action,
                    "target_id": action.target_id,
                    "quantity": action.quantity,
                    "face": action.face,
                    # Nominal per-move effect, attributed to who it lands on.
                    "actor_delta": actor_delta,
                    "target_delta": target_delta,
                    "thinking": action.thinking,
                    "was_defaulted": action.was_defaulted,
                    "mutual": False,
                    "betrayal": False,
                }
            )

        # Tag this turn's pacts (mutual HELP) and betrayals (HURT on last turn's
        # pact partner), so the feed can mark them without re-deriving in JS.
        helps = {
            a["agent_id"]: a["target_id"]
            for a in actions
            if a["action"] == "HELP" and a["target_id"]
        }
        this_mutual: set[frozenset[str]] = set()
        for a in actions:
            tgt = a["target_id"]
            if not tgt:
                continue
            pair = frozenset((a["agent_id"], tgt))
            if a["action"] == "HELP" and helps.get(tgt) == a["agent_id"]:
                a["mutual"] = True
                this_mutual.add(pair)
            elif a["action"] == "HURT" and pair in prev_mutual:
                a["betrayal"] = True
        prev_mutual = this_mutual

        for a in actions:
            paired_message = messages_by_agent.get(a["agent_id"])
            if paired_message is not None:
                a["message"] = paired_message["text"]
                a["message_thinking"] = paired_message["thinking"]
                a["message_was_defaulted"] = paired_message["was_defaulted"]
            else:
                a["message"] = ""
                a["message_thinking"] = ""
                a["message_was_defaulted"] = True

            if a["action"] == "HOARD":
                a["display_action"] = "Hoard"
                a["display_delta"] = a["actor_delta"]
            elif a["action"] == "HELP":
                a["display_action"] = "Help"
                a["display_delta"] = 8 if a["mutual"] else a["target_delta"]
            else:
                a["display_action"] = "HURT"
                a["display_delta"] = a["target_delta"]

        # Running in-round score (resets each round) → who leads, for the
        # play-by-play "lead change" beat.
        if t.round != inround_round:
            inround_round = t.round
            inround = {p.seat_name: 0 for p in players}
        inround = apply_inround_turn(inround, actions)
        # Highest score, ties broken alphabetically — deterministic.
        leader = min(inround, key=lambda k: (-inround[k], k)) if inround else None
        headline = _turn_headline(actions, prev_actions, leader, prev_leader, seq)
        prev_leader = leader
        prev_actions = actions

        history.append(
            {
                "seq": seq,
                "round": t.round,
                "turn": t.turn,
                "messages": messages,
                "actions": actions,
                # `actions` stays in submission order for the animation; the feed
                # renders `feed_actions` (highlights first) and `summary` (counts).
                "feed_actions": sorted(actions, key=_feed_sort_key),
                "summary": _turn_summary(actions),
                "groups": _turn_groups(actions),
                "headline": headline,
            }
        )

    return {
        "history": history,
        # The replay's turn data. Built here (not just in the full-page route) so
        # the live fragment carries fresh turns too — that's what lets an
        # already-open page extend the animation as new turns resolve, instead of
        # staying frozen at the turn count present when the page first loaded.
        "rc_data": _build_rc_data(scoreboard, history, g.turns_per_round, viewer_seat),
        # PD renders the animated robot-circle stage + narration dock above the
        # feed; games without that visual leave this off (see game.html).
        "show_replay_stage": True,
    }
