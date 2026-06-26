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

from app.games.hoard_hurt_help.match_summary import build_final_summary
from app.games.hoard_hurt_help.rules import (
    BETRAYAL_HURT_POINTS,
    HELP_POINTS,
    MUTUAL_HELP_BONUS,
    MUTUAL_HELP_FLOOR,
)
from app.games.hoard_hurt_help.scoring import apply_inround_turn
from app.games.hoard_hurt_help.viewer_headline import _turn_headline
from app.games.hoard_hurt_help.viewer_win_probs import _compute_round_win_probs
from app.games.viewer_common import (
    project_turn_messages,
    rc_envelope,
    rc_scoreboard_maps,
    rc_talk,
)
from app.models.match import GameState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.match import Match
    from app.models.player import Player
    from app.read_models.matches import TimelineTurn

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
    pact_values: set[int] = set()
    seen_pacts: set[frozenset[str]] = set()
    for a in actions:
        if a.get("mutual") and a["target_id"]:
            pair = frozenset((a["agent_id"], a["target_id"]))
            if pair not in seen_pacts:
                seen_pacts.add(pair)
                x, y = sorted(pair)
                pacts.append({"a": x, "b": y})
            if a.get("mutual_value") is not None:
                pact_values.add(a["mutual_value"])
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
        if len(pact_values) == 1:
            pact_delta = f"+{next(iter(pact_values))}"
        elif pact_values:
            pact_delta = f"+{min(pact_values)}–+{max(pact_values)}"  # decayed range
        else:
            pact_delta = f"+{HELP_POINTS + MUTUAL_HELP_BONUS}"  # fresh-pact fallback
        groups.append({"kind": "pact", "delta": pact_delta, "members": pacts})
    if helps:
        groups.append({"kind": "help", "delta": "+4", "members": helps})
    if hoards:
        groups.append({"kind": "hoard", "delta": "+2", "members": hoards})
    return groups


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
                val = mutuals[0]["delta"]  # decayed per-side value for this pact
                badge, cap = (
                    "The Pact",
                    f"{pair[0]} and {pair[1]} lock in a mutual pact — +{val} each.",
                )
            else:
                badge, cap = "The Pact", "Mutual pacts lock in."
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
    # Match-scoped count of how many times each pair has mutually helped, for the
    # mutual-help decay. Persists across rounds (does NOT reset per round).
    pact_counts: dict[frozenset[str], int] = {}
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
                    # This player's in-round score AS OF this turn (post-resolution).
                    # The feed chips show this per-turn value, not the live current
                    # score, so an old turn keeps showing the points it had then —
                    # they don't get overwritten by a later round's reset score.
                    "round_score_after": action.round_score_after,
                    # Nominal per-move effect, attributed to who it lands on.
                    "actor_delta": actor_delta,
                    "target_delta": target_delta,
                    "thinking": action.thinking,
                    "was_defaulted": action.was_defaulted,
                    "mutual": False,
                    "betrayal": False,
                    # HURT against a player who is HELPing you this same turn — lands for -8.
                    "betrayed_helper": False,
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
            elif a["action"] == "HURT":
                # Betraying a helper: HURT a player who is HELPing you this turn → -8.
                if helps.get(tgt) == a["agent_id"]:
                    a["betrayed_helper"] = True
                # Cross-turn betrayal: HURT last turn's pact partner.
                if pair in prev_mutual:
                    a["betrayal"] = True
        prev_mutual = this_mutual

        # Decayed per-side value for each of this turn's pacts (one per pair).
        # Computed once and shared by the display below and both apply_inround_turn
        # callers (the action dicts carry `mutual_value`), so the win-prob loop —
        # which resets its running score per round — still sees the match-scoped k.
        pact_value: dict[frozenset[str], int] = {}
        for pair in this_mutual:
            k = pact_counts.get(pair, 0)
            pact_value[pair] = max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k)
            pact_counts[pair] = k + 1

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
                if a["mutual"]:
                    value = pact_value[frozenset((a["agent_id"], a["target_id"]))]
                    a["mutual_value"] = value
                    a["display_delta"] = value
                else:
                    a["display_delta"] = a["target_delta"]
            else:
                a["display_action"] = "HURT"
                a["display_delta"] = (
                    -BETRAYAL_HURT_POINTS if a["betrayed_helper"] else a["target_delta"]
                )

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
                # Per-turn in-round score by seat, so the feed shows the points each
                # robot had AT THIS TURN (within its own round) — not the single
                # live current score painted on every turn. Every active seat acts
                # each turn, so keying on the actor covers actors and targets alike.
                "score_after": {
                    a["agent_id"]: a["round_score_after"] for a in actions
                },
                # `actions` stays in submission order for the animation; the feed
                # renders `feed_actions` (highlights first) and `summary` (counts).
                "feed_actions": sorted(actions, key=_feed_sort_key),
                "summary": _turn_summary(actions),
                "groups": _turn_groups(actions),
                "headline": headline,
            }
        )

    payload: dict[str, Any] = {
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

    # Finale: a completed match leads with the final scoreboard, not a replay
    # rewound to turn 1. Rounds won is the score; points are only the tiebreaker.
    # Built once on the completed page (a finished game has no live SSE swaps).
    if g.state == GameState.COMPLETED:
        winner_seat = next(
            (p.seat_name for p in players if p.id == g.winner_player_id), None
        )
        payload["final_summary"] = build_final_summary(
            total_rounds=g.total_rounds,
            scoreboard=scoreboard,
            total_scores={p.seat_name: p.total_round_score for p in players},
            history=history,
            winner_seat=winner_seat,
        )

    return payload
