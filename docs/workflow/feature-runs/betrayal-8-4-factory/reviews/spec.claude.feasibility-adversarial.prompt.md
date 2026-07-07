Review this spec artifact using a feasibility-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
Code context files are provided above. Before asserting any finding, check whether it is confirmed or refuted by the provided code. Each finding must include an evidence tag:
  [CODE-CONFIRMED] — the code directly supports this finding
  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)
  [UNVERIFIED] — relevant code was not provided; treat as lower confidence
Only assign HIGH severity to CODE-CONFIRMED findings.
The full review artifact text is included below in this prompt.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.
End your review with exactly one fenced JSON block — the machine-readable findings summary:
```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "<short title>", "detail": "<one-sentence detail>"}]}
```
Severity must be one of: CRITICAL, HIGH, MEDIUM, LOW. Include one entry per finding in your "## Findings" section.
If you found no issues, the block must be the affirmative clean bill exactly: {"reviewed": true, "findings": []}
This JSON block is required, is machine-parsed, and must be the last thing in your response.

Context: rules.py
"""Constants shipped to every agent and every player."""

from app.agent_prompt import RESPONSE_PROTOCOL

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 4  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side on a pair's FIRST mutual HELP this match
BETRAYAL_HURT_POINTS = 8  # HURT lands this hard when the target is HELPing you this turn
# Mutual help decays -1 each time the SAME pair repeats it within a match, flooring
# the pair's per-side total at MUTUAL_HELP_FLOOR (= HOARD_POINTS, so a farmed pact is
# no better than hoarding): total = max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k).
MUTUAL_HELP_FLOOR = 2

GAME_RULES_TEXT = f"""# Hoard-Hurt-Help — Official Rules (v4)

The goal is to win more rounds than any other agent over the course of the game.

## Actions

In the act phase, choose exactly one action. You cannot target yourself.

- **HOARD** — You gain +{HOARD_POINTS} points.
- **HELP [target]** — You gain 0 points; the target gains +{HELP_POINTS} points.
- **HURT [target]** — You gain 0 points; the target loses {HURT_POINTS} points.

## Stacking and combos

- **HELP stacks.** Multiple players HELPing the same target each contribute +{HELP_POINTS}.
- **HURT stacks.** Multiple players HURTing the same target each contribute -{HURT_POINTS}.
- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each the first time a pair does it.
- **Mutual-help decays.** Each time the *same pair* repeats a mutual help in a match, the bonus drops by 1. So that pair's net falls +{HELP_POINTS + MUTUAL_HELP_BONUS}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 1}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 2}, … down to a floor of +{MUTUAL_HELP_FLOOR} each (no better than HOARD). The count is match-wide, not per round. Helping a *fresh* partner resets to +{HELP_POINTS + MUTUAL_HELP_BONUS} — farming one ally pays less over time than spreading pacts around.
- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, your HURT lands for -{BETRAYAL_HURT_POINTS} instead of -{HURT_POINTS}. You still receive their +{HELP_POINTS} help, so betraying a helper is a +{HELP_POINTS} / -{BETRAYAL_HURT_POINTS} swing. (Moves resolve simultaneously, so this is a read on whether your target will help you.)
- HELP and HURT against the same target both resolve; the target's score moves by the net.

## Score floor

Round scores are clipped at 0. HURTing a player already at 0 still costs the attacker their turn but has no effect on the target.

## Round and game structure

- A game has **5 rounds**, each with **7 turns** (35 turns total).
- In-round score resets to 0 at the start of every round.
- The player with the highest in-round score after turn 7 wins the round and gets **1 round-win**. Ties split the round-win equally (1/N each).
- The player with the most round-wins after all 5 rounds wins the game.
- **Tiebreaker:** highest total in-round score summed across all rounds.

## Turn structure: talk, then act

Each turn has a talk phase followed by an act phase:

1. **Talk phase.** Broadcast one public message. Messages are revealed simultaneously once everyone has submitted or the deadline passes.
2. **Act phase.** After seeing all talk messages, choose your action. Actions resolve simultaneously.
"""

RULES_TEXT = f"""{GAME_RULES_TEXT}
## Response format

{RESPONSE_PROTOCOL}
"""

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."


def make_game_rules_text(total_rounds: int = 5, turns_per_round: int = 7) -> str:
    """Return semantic game rules with the actual round/turn counts."""
    if total_rounds == 5 and turns_per_round == 7:
        return GAME_RULES_TEXT
    return (
        GAME_RULES_TEXT
        .replace("**5 rounds**", f"**{total_rounds} rounds**")
        .replace("**7 turns**", f"**{turns_per_round} turns**")
        .replace("(35 turns total)", f"({total_rounds * turns_per_round} turns total)")
        .replace("after turn 7", f"after turn {turns_per_round}")
        .replace("after all 5 rounds", f"after all {total_rounds} rounds")
    )


def make_rules_text(total_rounds: int = 5, turns_per_round: int = 7) -> str:
    """Return official rules plus the canonical response contract."""
    return (
        f"{make_game_rules_text(total_rounds, turns_per_round)}"
        f"## Response format\n\n{RESPONSE_PROTOCOL}\n"
    )


Context: scoring.py
"""Prisoner's Dilemma turn scoring — HOARD/HELP/HURT payoffs.

The PD-specific per-turn math (raw deltas, mutual-help bonus, score floor).
Relocated verbatim from app/engine/resolver.py; the math is unchanged.
Read it with spec.md §5 alongside.
"""

from datetime import datetime, timezone

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.hoard_hurt_help.rules import (
    BETRAYAL_HURT_POINTS,
    DEFAULT_MISSED_MESSAGE,
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    MUTUAL_HELP_BONUS,
    MUTUAL_HELP_FLOOR,
)
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission


def mutual_help_counts(
    prior_turns: Iterable[Iterable[TurnSubmission]],
) -> dict[frozenset[int], int]:
    """How many prior turns each unordered pair mutually HELPed each other.

    `prior_turns` is one iterable of submissions per *resolved* turn. A pair is
    counted at most once per turn (mirroring `resolve_turn`'s same-turn guard).
    Only reciprocal HELP pairs count — HOARD/HURT/defaulted rows contribute 0.
    This is the single source of the decay counter `k`; reuse it, don't re-scan.
    """
    counts: dict[frozenset[int], int] = {}
    for subs in prior_turns:
        help_targets = {s.player_id: s.target_player_id for s in subs if s.action == "HELP"}
        seen: set[frozenset[int]] = set()
        for a, b in help_targets.items():
            if b is None or help_targets.get(b) != a:
                continue
            pair = frozenset({a, b})
            if pair not in seen:
                seen.add(pair)
                counts[pair] = counts.get(pair, 0) + 1
    return counts


async def current_pact_values(
    db: AsyncSession,
    match_id: str,
    player_id: int,
    other_player_ids: Iterable[int],
) -> dict[int, int]:
    """Current mutual-help pact value between `player_id` and each other player.

    The value is the per-side total (`max(MUTUAL_HELP_FLOOR, HELP_POINTS +
    MUTUAL_HELP_BONUS - k)`) a mutual HELP between that pair would pay EACH side
    right now. `k` — this match's decay counter for the pair — is derived from
    this match's *resolved* turn history via `mutual_help_counts`, the single
    source of that count; this function does not re-scan or re-derive it any
    other way. A pair with no prior mutual help this match (k not in the counts
    map) gets the fresh HELP_POINTS + MUTUAL_HELP_BONUS value. Only resolved
    turns are read, so — like `resolve_turn` — this is resume-safe: it has no
    in-memory-only state.
    """
    subs: list[TurnSubmission] = list(
        (
            await db.execute(
                select(TurnSubmission)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .where(Turn.match_id == match_id, Turn.resolved_at.is_not(None))
                .order_by(TurnSubmission.turn_id)
            )
        )
        .scalars()
        .all()
    )
    by_turn: dict[int, list[TurnSubmission]] = {}
    for s in subs:
        by_turn.setdefault(s.turn_id, []).append(s)
    counts = mutual_help_counts(by_turn.values())
    return {
        other_id: max(
            MUTUAL_HELP_FLOOR,
            HELP_POINTS + MUTUAL_HELP_BONUS - counts.get(frozenset({player_id, other_id}), 0),
        )
        for other_id in other_player_ids
    }


async def resolve_turn(db: AsyncSession, turn: Turn) -> None:
    """Resolve one turn: materialize submissions, apply payoffs, persist deltas.

    Order matters and matches spec.md §5:
      1. Default any missing submission to HOARD (was_defaulted=True).
      2. Compute raw deltas (Hoard +2, Help +4 to target, Hurt -4 to target).
      3. Add the mutual-help bonus for any A↔B pair, DECAYED by how many times that
         same pair already mutually helped this match (max(2, 8-k) per side; floor
         at the Hoard value). k is derived from prior resolved turns.
      4. Apply the score floor at 0 to the FINAL per-player delta, not per-hurt.
      5. Persist post-floor `points_delta` and `round_score_after`.
      6. Mark turn resolved.
    """
    # Players in this game.
    players: list[Player] = list(
        (await db.execute(select(Player).where(Player.match_id == turn.match_id)))
        .scalars()
        .all()
    )

    # Per-pair mutual-help decay: count how many times each pair already mutually
    # helped in this match's PRIOR resolved turns (the current turn isn't resolved
    # yet, and is excluded by id). Derived from history so it survives a DB resume.
    prior_subs: list[TurnSubmission] = list(
        (
            await db.execute(
                select(TurnSubmission)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .where(
                    Turn.match_id == turn.match_id,
                    Turn.resolved_at.is_not(None),
                    Turn.id != turn.id,
                )
                .order_by(TurnSubmission.turn_id)
            )
        )
        .scalars()
        .all()
    )
    prior_by_turn: dict[int, list[TurnSubmission]] = {}
    for s in prior_subs:
        prior_by_turn.setdefault(s.turn_id, []).append(s)
    prior_counts = mutual_help_counts(prior_by_turn.values())

    # Materialize submissions, defaulting missing ones to HOARD.
    submissions: list[TurnSubmission] = list(
        (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)))
        .scalars()
        .all()
    )
    submitted_player_ids = {s.player_id for s in submissions}
    for p in players:
        if p.id not in submitted_player_ids:
            default = TurnSubmission(
                turn_id=turn.id,
                player_id=p.id,
                action="HOARD",
                target_player_id=None,
                message=DEFAULT_MISSED_MESSAGE,
                was_defaulted=True,
                submitted_at=None,
            )
            db.add(default)
            submissions.append(default)
    await db.flush()

    # Raw deltas (pre-floor).
    delta: dict[int, int] = {p.id: 0 for p in players}

    # Who each HELPer targeted — needed both for the mutual-help bonus below and
    # to detect a betrayal HURT (HURTing someone who is HELPing you this turn).
    help_targets = {
        s.player_id: s.target_player_id for s in submissions if s.action == "HELP"
    }

    for s in submissions:
        if s.action == "HOARD":
            delta[s.player_id] += HOARD_POINTS
        elif s.action == "HELP" and s.target_player_id in delta:
            delta[s.target_player_id] += HELP_POINTS
        elif s.action == "HURT" and s.target_player_id in delta:
            # Betraying a helper: HURTing a player who is HELPing you this same
            # turn lands for BETRAYAL_HURT_POINTS instead of the base HURT_POINTS.
            betrayed_helper = help_targets.get(s.target_player_id) == s.player_id
            delta[s.target_player_id] -= (
                BETRAYAL_HURT_POINTS if betrayed_helper else HURT_POINTS
            )

    # Mutual-help bonus, DECAYED per pair: for each HELP pair where both helped
    # each other, add the bonus to each side once. The bonus shrinks by 1 for each
    # prior mutual help by this same pair (k), flooring the pair's per-side total at
    # MUTUAL_HELP_FLOOR: total = base HELP_POINTS + bonus = max(MUTUAL_HELP_FLOOR, 8-k).
    seen_pairs: set[frozenset[int]] = set()
    for a, b in help_targets.items():
        if b is None:
            continue
        if help_targets.get(b) == a:
            pair = frozenset({a, b})
            if pair not in seen_pairs:
                k = prior_counts.get(pair, 0)
                bonus = max(MUTUAL_HELP_FLOOR - HELP_POINTS, MUTUAL_HELP_BONUS - k)
                delta[a] += bonus
                delta[b] += bonus
                seen_pairs.add(pair)

    # Apply floor on final delta and persist.
    sub_by_player: dict[int, TurnSubmission] = {s.player_id: s for s in submissions}
    for p in players:
        new_score = p.current_round_score + delta[p.id]
        if new_score < 0:
            new_score = 0
        actual_delta = new_score - p.current_round_score
        p.current_round_score = new_score
        s = sub_by_player[p.id]
        s.points_delta = actual_delta
        s.round_score_after = new_score

    turn.resolved_at = datetime.now(timezone.utc)
    await db.commit()


def apply_inround_turn(
    inround: Mapping[str, int], actions: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    """Return a new in-round score map after applying one turn's actions.

    This is the *viewer's* running-score view — used for lead tracking and the
    win-probability features. It floors each HURT individually and credits a
    mutual-help actor the decayed per-side total (`mutual_value` on the action,
    falling back to the fresh-pact HELP_POINTS + MUTUAL_HELP_BONUS if absent). A
    HURT against a player who HELPs the attacker this same turn lands for
    BETRAYAL_HURT_POINTS, mirroring `resolve_turn`. It is a display approximation
    and is deliberately distinct from `resolve_turn`, which is authoritative and
    floors the summed per-player delta. Keep them separate; do not route
    resolution through this helper.

    Action dicts use keys: "action", "agent_id", optional "target_id",
    optional "mutual", optional "mutual_value" (the decayed per-side total — the
    caller computes the per-pair decay; this helper has no match history).
    """
    new_inround = dict(inround)
    mutual_help = HELP_POINTS + MUTUAL_HELP_BONUS
    # Who each HELPer targeted — to detect a betrayal HURT (HURTing a same-turn helper).
    help_targets = {
        a["agent_id"]: a.get("target_id") for a in actions if a["action"] == "HELP"
    }
    for a in actions:
        action = a["action"]
        actor = a["agent_id"]
        target = a.get("target_id")
        mutual = a.get("mutual", False)
        if action == "HOARD":
            new_inround[actor] = new_inround.get(actor, 0) + HOARD_POINTS
        elif action == "HELP" and mutual:
            new_inround[actor] = new_inround.get(actor, 0) + a.get("mutual_value", mutual_help)
        elif action == "HELP" and target:
            new_inround[target] = new_inround.get(target, 0) + HELP_POINTS
        elif action == "HURT" and target:
            damage = (
                BETRAYAL_HURT_POINTS if help_targets.get(target) == actor else HURT_POINTS
            )
            new_inround[target] = max(0, new_inround.get(target, 0) - damage)
    return new_inround


Context: viewer.py
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
    viewer_seat: str | None = None,
) -> str:
    """Serialize game history as the robot-circle viewer JSON format."""
    agents, labels, bots, owners = rc_scoreboard_maps(scoreboard)
    # agent_id → provider label (Claude/Gemini/…) that actually played the seat,
    # for the standings rail's per-competitor badge. Omitted for bots and seats
    # not yet served (no provider). PD-only enrichment on top of the shared maps.
    providers = {r["agent_id"]: r["provider"] for r in scoreboard if r.get("provider")}

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

    # Which seats are bots (scripted opponents). The feed uses this to hide bots'
    # auto-generated "thinking" notes — canned strategy text, not a model's real
    # reasoning. Real agents' thinking (when they submit any) still shows.
    bot_ids = {r["agent_id"] for r in scoreboard if r.get("is_bot")}

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
                    # Bots' "thinking" is canned strategy text, so the feed hides
                    # it (turn_block.html) — real agents' reasoning still shows.
                    "is_bot": action.agent_id in bot_ids,
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
        "rc_data": _build_rc_data(scoreboard, history, viewer_seat),
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


Context: game.py
"""Prisoner's Dilemma — `hoard-hurt-help`.

It implements the `GameModule` contract using this module's own PD rules and
scoring (`app.games.hoard_hurt_help.rules` / `.scoring`) and delegating the
game-agnostic talk/round/game finalization to `app.engine.resolver`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.agent_prompt import make_agent_base_prompt
from app.engine import resolver
from app.games.base import (
    BaseGameModule,
    GameConfig,
    GameError,
    GameTheme,
    StrategyPreset,
)
from app.games.hoard_hurt_help import scoring
from app.games.hoard_hurt_help.rules import (
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    MUTUAL_HELP_FLOOR,
    make_game_rules_text,
    make_rules_text,
)
from app.games.hoard_hurt_help.strategy import PD_DEFAULT_STRATEGY, PD_STRATEGY_PRESETS
from app.models.player import Player
from app.models.turn import TurnMessage, TurnSubmission

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.engine.game_insights import RoundDetail, SeasonOverview
    from app.engine.game_records import ActionRecord, PlayerRecord
    from app.models.match import Match
    from app.models.turn import Turn
    from app.read_models.matches import TimelineTurn
    from app.schemas.agent import BoardSignals

_VALID_ACTIONS = {"HOARD", "HELP", "HURT"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HoardHurtHelp(BaseGameModule):
    """The Prisoner's Dilemma game module."""

    game_type = "hoard-hurt-help"

    def display_name(self) -> str:
        return "Hoard · Hurt · Help"

    def tagline(self) -> str:
        return "A multiplayer game of trust and betrayal for AI agents."

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=5,
            turns_per_round=7,
            # Act-phase window. Reasoning models (e.g. gpt-5.4-mini) can take ~50s
            # to decide a move; 75s clears them with margin. The talk phase is
            # capped shorter separately (scheduler TALK_DEADLINE_SECONDS).
            per_turn_deadline_seconds=75,
            min_players=6,
            max_players=10,
        )

    def action_names(self) -> tuple[str, ...]:
        # Canonical display order the insight engines tally moves in:
        # HOARD (keep), HELP (cooperate), HURT (attack).
        return ("HOARD", "HELP", "HURT")

    def rules_text(self, total_rounds: int = 5, turns_per_round: int = 7) -> str:
        return make_rules_text(total_rounds, turns_per_round)

    def semantic_rules_text(self, total_rounds: int = 5, turns_per_round: int = 7) -> str:
        return make_game_rules_text(total_rounds, turns_per_round)

    def strategy_presets(self) -> list[StrategyPreset]:
        return PD_STRATEGY_PRESETS

    def default_strategy(self) -> str:
        return PD_DEFAULT_STRATEGY

    def agent_base_prompt(
        self,
        *,
        your_agent_id: str,
        all_agent_ids: list[str],
        total_rounds: int = 5,
        turns_per_round: int = 7,
    ) -> str:
        return make_agent_base_prompt(
            your_agent_id=your_agent_id,
            all_agent_ids=all_agent_ids,
            rules=make_game_rules_text(total_rounds, turns_per_round),
        )

    def validate_move(
        self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]
    ) -> None:
        action = str(move.get("action", "")).upper()
        target = move.get("target_id")
        if action not in _VALID_ACTIONS:
            raise GameError("INVALID_ACTION", "action must be HOARD, HELP, or HURT.")
        if action == "HOARD":
            if target is not None:
                raise GameError(
                    "TARGET_NOT_ALLOWED_FOR_HOARD", "HOARD must not have a target."
                )
            return
        if target is None:
            raise GameError("MISSING_TARGET", "HELP/HURT requires target_id.")
        if target == your_agent_id:
            raise GameError(
                "INVALID_TARGET", "Cannot target self.", {"reason": "self_target"}
            )
        if target not in all_agent_ids:
            raise GameError(
                "INVALID_TARGET",
                "Target not in this game.",
                {"reason": "unknown_agent"},
            )

    async def record_submission(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
        is_connector_fallback: bool = False,
    ) -> None:
        action = str(move["action"]).upper()
        target_id = move.get("target_id")
        target_player_id: int | None = None
        if target_id is not None:
            target = (
                await db.execute(
                    select(Player).where(
                        Player.match_id == turn.match_id, Player.agent_id == target_id
                    )
                )
            ).scalar_one_or_none()
            target_player_id = target.id if target is not None else None
        message = str(move.get("message", ""))
        thinking = str(move.get("thinking", ""))
        # Connector fallbacks reuse the existing was_defaulted column so they are
        # identifiable in the DB without a migration. A genuine move clears the flag.
        was_defaulted = is_connector_fallback
        if existing is not None:
            existing.action = action
            existing.target_player_id = target_player_id
            existing.message = message
            existing.thinking = thinking
            existing.was_defaulted = was_defaulted
            existing.submitted_at = _now()
        else:
            db.add(
                TurnSubmission(
                    turn_id=turn.id,
                    player_id=player.id,
                    action=action,
                    target_player_id=target_player_id,
                    message=message,
                    thinking=thinking,
                    was_defaulted=was_defaulted,
                    submitted_at=_now(),
                )
            )

    async def record_message(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        message: str,
        thinking: str,
        *,
        existing: TurnMessage | None,
        is_connector_fallback: bool = False,
    ) -> None:
        # Connector fallbacks reuse the existing was_defaulted column.
        was_defaulted = is_connector_fallback
        if existing is not None:
            existing.text = message
            existing.thinking = thinking
            existing.was_defaulted = was_defaulted
            existing.submitted_at = _now()
        else:
            db.add(
                TurnMessage(
                    turn_id=turn.id,
                    player_id=player.id,
                    text=message,
                    thinking=thinking,
                    was_defaulted=was_defaulted,
                    submitted_at=_now(),
                )
            )

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None:
        await scoring.resolve_turn(db, turn)

    async def award_round(self, db: AsyncSession, game: Match, round_num: int) -> None:
        await resolver.award_round_winners(db, game, round_num)

    async def finalize(self, db: AsyncSession, game: Match) -> None:
        await resolver.finalize_game(db, game)

    async def default_move(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        # A missed deadline records HOARD (keep, target nobody) — PD's long-standing
        # default move, made explicit now that the base no longer assumes it.
        return {"action": "HOARD", "target_id": None}

    async def private_state_for(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        # `pact_values`: what a mutual HELP with each other seat would pay EACH
        # side RIGHT NOW — already decayed by that pair's prior mutual helps this
        # match. Lets an agent read the current per-pair decay counter `k` off
        # the payload instead of re-scanning full match history to recount it
        # (feature `mutual-help-pact-value`; k itself comes from
        # `scoring.mutual_help_counts`, derived from resolved turns so it's
        # resume-safe).
        all_players = (
            (await db.execute(select(Player).where(Player.match_id == match.id)))
            .scalars()
            .all()
        )
        other_players = [p for p in all_players if p.id != player.id]
        if not other_players:
            return {}
        values = await scoring.current_pact_values(
            db, match.id, player.id, (p.id for p in other_players)
        )
        return {
            "pact_values": {p.seat_name: values[p.id] for p in other_players},
            "pact_values_note": (
                "What a mutual HELP with this agent would pay EACH side right "
                "now (decays per repeat mutual-help pair this match; floors at "
                f"{MUTUAL_HELP_FLOOR})."
            ),
        }

    def move_effect(self, action: str) -> tuple[int, int | None]:
        a = action.upper()
        if a == "HOARD":
            return HOARD_POINTS, None
        if a == "HELP":
            return 0, HELP_POINTS
        if a == "HURT":
            return 0, -HURT_POINTS
        return 0, None

    async def build_replay_view(
        self,
        db: AsyncSession,
        match: Match,
        players: list[Player],
        scoreboard: list[dict[str, Any]],
        timeline: list[TimelineTurn],
        viewer_seat: str | None,
    ) -> dict[str, Any]:
        from app.games.hoard_hurt_help.viewer import build_pd_replay_view

        return await build_pd_replay_view(
            db, match, players, scoreboard, timeline, viewer_seat
        )

    def viewer_fragment(self) -> str:
        return "fragments/pd_live_region.html"

    def board_signals(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        current_round: int,
    ) -> BoardSignals:
        from app.games.hoard_hurt_help.board_signals import compute_board_signals

        return compute_board_signals(players, actions, current_round)

    def season_overview(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        total_rounds: int,
        current_round: int,
        game_active: bool,
    ) -> SeasonOverview:
        from app.games.hoard_hurt_help.insights import season_overview

        return season_overview(players, actions, total_rounds, current_round, game_active)

    def round_detail(
        self,
        round_num: int,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
    ) -> RoundDetail:
        from app.games.hoard_hurt_help.insights import round_detail

        return round_detail(round_num, players, actions)

    def theme(self) -> GameTheme:
        # The flagship game wears the platform's warm orange, plus the move trio
        # (hoard amber / help green / hurt red) as its semantic colors and a
        # faintly warm surface so its pages read as "this game" inside the shared
        # Agent Ludum shell. Only content tokens here — never chrome.
        return GameTheme(
            key=self.game_type,
            vars={
                "--brand": "#e2640e",
                "--brand-2": "#5b4fd6",
                "--accent": "#b8861a",
                "--on-brand": "#fff6ec",
                "--surface": "#fbf7f1",
                "--surface-2": "#f3ece1",
                "--hoard": "#b07e0d",
                "--help": "#1f8a5b",
                "--hurt": "#c1452f",
            },
        )


Context: test_resolver.py
"""Payoff math, mutual bonus, score floor, missed-turn default.

Every test creates a minimal in-memory game with N players and one open turn,
materializes submissions, calls resolve_turn, then asserts the deltas.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.resolver import award_round_winners, finalize_game
from app.games.hoard_hurt_help.rules import DEFAULT_MISSED_MESSAGE
from app.games.hoard_hurt_help.scoring import resolve_turn
from app.models import Match, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot


# --- Fixtures ---


async def _make_game_with_players(db: AsyncSession, n: int) -> tuple[Match, list[Player]]:
    """Create a game in ACTIVE state with n players, current_round_score=0."""
    game = Match(
        id="G_TEST",
        name="test",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
    )
    db.add(game)
    await db.flush()

    players = []
    for i in range(n):
        u = User(google_sub=f"sub-{i}", email=f"u{i}@test.com", name=f"u{i}")
        db.add(u)
        await db.flush()
        agent, _ = await make_bot(db, u, name=f"AI_{i}")
        p = Player(
            match_id=game.id,
            user_id=u.id,
            agent_id=agent.id,
            seat_name=f"AI_{i}",
        )
        db.add(p)
        await db.flush()
        players.append(p)

    await db.commit()
    return game, players


async def _open_turn(db: AsyncSession, game: Match, round_num: int = 1, turn_num: int = 1) -> Turn:
    now = datetime.now(timezone.utc)
    t = Turn(
        match_id=game.id,
        round=round_num,
        turn=turn_num,
        turn_token=f"tk_{round_num}_{turn_num}",
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _submit(
    db: AsyncSession,
    turn: Turn,
    player: Player,
    action: str,
    target: Player | None = None,
    message: str = "",
):
    s = TurnSubmission(
        turn_id=turn.id,
        player_id=player.id,
        action=action,
        target_player_id=target.id if target else None,
        message=message,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.commit()


# --- Tests ---


async def test_single_hoard(db):
    game, [p0] = await _make_game_with_players(db, 1)
    turn = await _open_turn(db, game)
    await _submit(db, turn, p0, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(p0)
    assert p0.current_round_score == 2


async def test_single_help(db):
    """A Helps B → A gets 0, B gets +4."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HOARD")  # B Hoards to keep test simple
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0
    assert b.current_round_score == 2 + 4  # Hoard +2 plus Help received


async def test_single_hurt(db):
    """A Hurts B → A gets 0, B gets -4 (clipped to 0 from 0)."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0
    # B starts at 0, Hoard +2, Hurt -4 → max(0, -2) = 0
    assert b.current_round_score == 0


async def test_help_stacks(db):
    """5 helps on one target → +20 to target."""
    game, players = await _make_game_with_players(db, 6)
    target = players[0]
    helpers = players[1:]
    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")
    for h in helpers:
        await _submit(db, turn, h, "HELP", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    # Target: +2 hoard + 5*4 help = 22
    assert target.current_round_score == 22


async def test_hurt_stacks_with_floor(db):
    """5 hurts on one target → floored at 0."""
    game, players = await _make_game_with_players(db, 6)
    target = players[0]
    attackers = players[1:]
    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")
    for a in attackers:
        await _submit(db, turn, a, "HURT", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    # Target: +2 hoard - 5*4 hurt = -18, floored to 0
    assert target.current_round_score == 0


async def test_mutual_help_bonus(db):
    """A Helps B and B Helps A → each ends +8."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 8
    assert b.current_round_score == 8


async def test_mutual_bonus_does_not_double(db):
    """If A Helps B, B Helps A, and C also Helps A, mutual bonus only counts the A↔B pair.

    A receives: +4 from B (base) + +4 from C (base) + +4 mutual = 12
    B receives: +4 from A (base) + +4 mutual = 8
    C receives: 0 (nobody Helped C back)
    """
    game, [a, b, c] = await _make_game_with_players(db, 3)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, c, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    assert a.current_round_score == 12
    assert b.current_round_score == 8
    assert c.current_round_score == 0


async def test_score_floor_on_final_delta(db):
    """Floor applies to the final summed delta, not per incoming Hurt.

    Player starts at 3, gets two -4 Hurts and one +4 Help in same turn.
    Raw: 3 - 4 - 4 + 4 = -1, floored to 0.
    """
    game, [target, h1, h2, helper] = await _make_game_with_players(db, 4)
    target.current_round_score = 3
    await db.commit()

    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")  # +2 added
    await _submit(db, turn, h1, "HURT", target=target)
    await _submit(db, turn, h2, "HURT", target=target)
    await _submit(db, turn, helper, "HELP", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    # 3 + 2 (hoard) + 4 (help) - 4 - 4 (two hurts) = 1, no floor needed
    assert target.current_round_score == 1


async def test_hurt_against_zero_target(db):
    """HURT against 0-score target: target stays at 0; attacker gets 0 (not +2)."""
    game, [a, b] = await _make_game_with_players(db, 2)
    # B starts at 0.
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")  # B hoards but is also being hurt
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0  # used turn on HURT, no Hoard
    assert b.current_round_score == 0  # +2 - 4, clipped to 0


async def test_betraying_a_helper_hurts_for_eight(db):
    """HURTing a player who HELPs you this turn lands for -8, not -4.

    B HELPs A (A gets +4). A HURTs B → betrays the helper for -8 to B.
    A ends +4; B (starting at 10) ends 10 - 8 = 2.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    b.current_round_score = 10
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 4  # +4 from B's help (A's HURT gives A nothing)
    assert b.current_round_score == 2  # 10 - 8 betrayal


async def test_hurt_non_helper_stays_four(db):
    """A normal HURT (target did NOT help the attacker) still lands for -4.

    B HOARDs (does not help A). A HURTs B → base -4, not the betrayal -8.
    B (starting at 10) ends 10 + 2 (hoard) - 4 = 8.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    b.current_round_score = 10
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(b)
    assert b.current_round_score == 8  # 10 + 2 - 4, NOT -8


async def test_betrayal_only_for_the_helped_attacker(db):
    """Only the attacker the victim HELPed lands the -8; other attackers stay -4.

    B HELPs A. A HURTs B (betrayal -8). C HURTs B (normal -4, B never helped C).
    B (starting at 20) ends 20 - 8 - 4 = 8. A gets +4 from B's help.
    """
    game, [a, b, c] = await _make_game_with_players(db, 3)
    b.current_round_score = 20
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, c, "HURT", target=b)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 4
    assert b.current_round_score == 8  # 20 - 8 (A betrayal) - 4 (C normal)


async def test_missed_turn_defaults_to_hoard(db):
    """A player with no submission gets defaulted to Hoard with canonical message."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HOARD")
    # B does not submit.
    await resolve_turn(db, turn)
    await db.refresh(b)
    assert b.current_round_score == 2

    # The defaulted submission row exists with the canonical message.
    from sqlalchemy import select
    sub = (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id, TurnSubmission.player_id == b.id
            )
        )
    ).scalar_one()
    assert sub.was_defaulted is True
    assert sub.action == "HOARD"
    assert sub.message == DEFAULT_MISSED_MESSAGE


async def test_round_award_single_winner(db):
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 10
    b.current_round_score = 6
    c.current_round_score = 4
    await db.commit()
    await award_round_winners(db, game, 1)
    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    assert a.total_round_wins == 1.0
    assert b.total_round_wins == 0
    assert c.total_round_wins == 0
    assert a.total_round_score == 10
    assert b.total_round_score == 6
    assert c.total_round_score == 4


async def test_round_award_three_way_tie(db):
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 8
    b.current_round_score = 8
    c.current_round_score = 8
    await db.commit()
    await award_round_winners(db, game, 1)
    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    assert a.total_round_wins == pytest.approx(1 / 3)
    assert b.total_round_wins == pytest.approx(1 / 3)
    assert c.total_round_wins == pytest.approx(1 / 3)


async def test_round_award_is_idempotent(db):
    """Awarding the same round twice (a mid-game restart re-entering the loop at
    an already-finished round) must NOT double-count wins or scores."""
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 10
    b.current_round_score = 6
    c.current_round_score = 4
    await db.commit()

    await award_round_winners(db, game, 1)
    await award_round_winners(db, game, 1)  # resume re-entry — must be a no-op

    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    await db.refresh(game)
    assert a.total_round_wins == 1.0
    assert b.total_round_wins == 0
    assert c.total_round_wins == 0
    assert a.total_round_score == 10
    assert b.total_round_score == 6
    assert c.total_round_score == 4
    assert game.rounds_awarded == 1


async def test_round_award_accumulates_across_rounds(db):
    """Consecutive rounds each award once and advance rounds_awarded."""
    game, [a, b] = await _make_game_with_players(db, 2)
    a.current_round_score = 5  # a wins round 1
    b.current_round_score = 3
    await db.commit()
    await award_round_winners(db, game, 1)

    a.current_round_score = 2  # round 2 (scores reset then re-earned); b wins
    b.current_round_score = 9
    await db.commit()
    await award_round_winners(db, game, 2)

    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(game)
    assert game.rounds_awarded == 2
    assert a.total_round_score == 7  # 5 + 2
    assert b.total_round_score == 12  # 3 + 9
    assert a.total_round_wins == 1.0  # round 1
    assert b.total_round_wins == 1.0  # round 2


async def test_finalize_game_with_tiebreaker(db):
    """Two players tie on round wins; tiebreaker is total in-round score."""
    game, [a, b] = await _make_game_with_players(db, 2)
    a.total_round_wins = 5
    a.total_round_score = 120
    b.total_round_wins = 5
    b.total_round_score = 130
    await db.commit()
    await finalize_game(db, game)
    await db.refresh(game)
    assert game.state == GameState.COMPLETED
    assert game.winner_player_id == b.id


# --- One shared finish-order key (finalize_game winner == final_placement) ---


class _Standing:
    """A minimal stand-in with the two fields the finish-order sorts read."""

    def __init__(self, pid: int, wins: float, score: int) -> None:
        self.id = pid
        self.total_round_wins = wins
        self.total_round_score = score

    def __repr__(self) -> str:  # readable assertion diffs
        return f"P{self.id}(w={self.total_round_wins}, s={self.total_round_score})"


def test_finish_order_key_matches_both_old_sorts() -> None:
    """The shared key reproduces BOTH old encodings — winner pick and placement.

    Old finalize_game winner sort: ascending on (-wins, -score) — stable, so
    full ties keep input order. Old final_placement sort: (wins, score) with
    reverse=True — Python's reverse sort is also stable, so full ties keep
    input order too. The shared key must reproduce both orderings exactly,
    including tie order, for every input permutation.
    """
    from itertools import permutations

    from app.engine.resolver import finish_order_sort_key

    cases: list[list[_Standing]] = [
        # Equal round wins; score breaks the tie.
        [_Standing(1, 5.0, 120), _Standing(2, 5.0, 130), _Standing(3, 5.0, 120)],
        # Equal round wins AND equal score — a full tie (input order decides).
        [_Standing(1, 2.0, 40), _Standing(2, 2.0, 40), _Standing(3, 2.0, 40)],
        # Mixed: distinct wins, partial score ties, fractional wins.
        [
            _Standing(1, 1.5, 30),
            _Standing(2, 3.0, 10),
            _Standing(3, 1.5, 30),
            _Standing(4, 0.0, 99),
        ],
    ]
    for case in cases:
        for players in permutations(case):
            seeded = list(players)
            old_winner_order = sorted(
                seeded, key=lambda p: (-p.total_round_wins, -p.total_round_score)
            )
            old_placement_order = sorted(
                seeded,
                key=lambda p: (p.total_round_wins, p.total_round_score),
                reverse=True,
            )
            shared_order = sorted(seeded, key=finish_order_sort_key)
            assert [p.id for p in shared_order] == [p.id for p in old_winner_order]
            assert [p.id for p in shared_order] == [p.id for p in old_placement_order]


async def test_finalize_game_winner_matches_final_placement_on_full_tie(db):
    """Equal round wins AND equal score: winner == final_placement[0].

    Both paths query players the same way and sort with the same stable key, so
    on a full tie both must pick the same (first-seeded) player.
    """
    from app.games.hoard_hurt_help.game import HoardHurtHelp

    game, [a, b] = await _make_game_with_players(db, 2)
    a.total_round_wins = 3
    a.total_round_score = 50
    b.total_round_wins = 3
    b.total_round_score = 50
    await db.commit()

    placement = await HoardHurtHelp().final_placement(db, game)
    await finalize_game(db, game)
    await db.refresh(game)
    assert game.state == GameState.COMPLETED
    # Full tie: the stable sorts keep seed order, so the first-seeded player
    # wins — and the winner is exactly the head of final_placement.
    assert game.winner_player_id == a.id
    assert game.winner_player_id == placement[0]


# --- Mutual-help decay (feature mutual-help-decay, Slice 1) ---


class _FakeSub:
    def __init__(self, player_id: int, action: str, target: int | None = None) -> None:
        self.player_id = player_id
        self.action = action
        self.target_player_id = target


def test_mutual_help_counts_helper() -> None:
    """Pure counter: per unordered pair, how many prior turns they mutually helped."""
    from app.games.hoard_hurt_help.scoring import mutual_help_counts

    turns = [
        [_FakeSub(1, "HELP", 2), _FakeSub(2, "HELP", 1), _FakeSub(3, "HOARD")],  # 1<->2
        [_FakeSub(1, "HELP", 2), _FakeSub(2, "HELP", 1)],  # 1<->2 again
        [_FakeSub(1, "HELP", 2), _FakeSub(2, "HELP", 3), _FakeSub(3, "HELP", 2)],  # 2<->3
    ]
    counts = mutual_help_counts(turns)
    assert counts[frozenset({1, 2})] == 2
    assert counts[frozenset({2, 3})] == 1
    assert frozenset({1, 3}) not in counts  # one-directional help never counts


async def test_mutual_help_decays_to_floor(db):
    """A pair's repeated mutual help pays 8,7,6,5,4,3,2,2 — decays -1/repeat, floor 2.

    k is re-derived from the persisted prior turns on every resolve, so this also
    exercises the resume-safe path (no in-memory state to lose).
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    prev = 0
    for i, expected in enumerate([8, 7, 6, 5, 4, 3, 2, 2]):
        turn = await _open_turn(db, game, round_num=1, turn_num=i + 1)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await resolve_turn(db, turn)
        await db.refresh(a)
        assert a.current_round_score - prev == expected, (i, expected)
        prev = a.current_round_score


async def test_decay_persists_across_rounds(db):
    """k counts prior mutual-help turns match-wide — it does NOT reset each round."""
    game, [a, b] = await _make_game_with_players(db, 2)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await resolve_turn(db, t1)
    await db.refresh(a)
    assert a.current_round_score == 8  # k=0 → +8
    base = a.current_round_score

    t2 = await _open_turn(db, game, round_num=3, turn_num=1)
    await _submit(db, t2, a, "HELP", target=b)
    await _submit(db, t2, b, "HELP", target=a)
    await resolve_turn(db, t2)
    await db.refresh(a)
    assert a.current_round_score - base == 7  # k=1 even though it's a later round


async def test_fresh_partner_resets_decay(db):
    """A farmed pact decays, but a brand-new partner starts fresh at +8."""
    game, [a, b, c] = await _make_game_with_players(db, 3)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await _submit(db, t1, c, "HOARD")
    await resolve_turn(db, t1)
    await db.refresh(a)
    base = a.current_round_score  # 8 from the A↔B pact

    t2 = await _open_turn(db, game, round_num=1, turn_num=2)
    await _submit(db, t2, a, "HELP", target=c)  # fresh partner
    await _submit(db, t2, c, "HELP", target=a)
    await _submit(db, t2, b, "HOARD")
    await resolve_turn(db, t2)
    await db.refresh(a)
    assert a.current_round_score - base == 8  # A↔C is a fresh pair, k=0


async def test_decay_is_per_pair_independent(db):
    """Two pacts at the same table decay on their own counters."""
    game, [a, b, c, d] = await _make_game_with_players(db, 4)
    for turn_num, expected in [(1, 8), (2, 7)]:
        turn = await _open_turn(db, game, round_num=1, turn_num=turn_num)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await _submit(db, turn, c, "HELP", target=d)
        await _submit(db, turn, d, "HELP", target=c)
        await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(c)
    # Both pairs went 8 then 7 → each side totals 15, independently.
    assert a.current_round_score == 15
    assert c.current_round_score == 15


async def test_prior_hoard_turn_does_not_count_toward_k(db):
    """A prior non-mutual (HOARD/defaulted) turn leaves k=0 — first pact still pays 8."""
    game, [a, b] = await _make_game_with_players(db, 2)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HOARD")
    # b never submits → defaulted to HOARD
    await resolve_turn(db, t1)
    await db.refresh(a)
    assert a.current_round_score == 2  # just the hoard

    t2 = await _open_turn(db, game, round_num=1, turn_num=2)
    await _submit(db, t2, a, "HELP", target=b)
    await _submit(db, t2, b, "HELP", target=a)
    await resolve_turn(db, t2)
    await db.refresh(a)
    assert a.current_round_score == 2 + 8  # k=0 → fresh +8


# --- current_pact_values (feature mutual-help-pact-value) ---


async def test_current_pact_values_fresh_pair_shows_8(db):
    """A pair with no resolved turns yet shows the un-decayed +8 value."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b] = await _make_game_with_players(db, 2)
    values = await current_pact_values(db, game.id, a.id, [b.id])
    assert values == {b.id: 8}


async def test_current_pact_values_after_one_mutual_help_shows_7(db):
    """After one resolved mutual help (k=1), the pair's live value drops to 7."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b] = await _make_game_with_players(db, 2)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await resolve_turn(db, t1)

    values = await current_pact_values(db, game.id, a.id, [b.id])
    assert values == {b.id: 7}
    # Symmetric: B's live value with A is the same.
    assert await current_pact_values(db, game.id, b.id, [a.id]) == {a.id: 7}


async def test_current_pact_values_floors_at_2(db):
    """After enough repeats the pair's live value floors at MUTUAL_HELP_FLOOR (2)."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b] = await _make_game_with_players(db, 2)
    for i in range(8):  # k will reach 8, well past the floor
        turn = await _open_turn(db, game, round_num=1, turn_num=i + 1)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await resolve_turn(db, turn)

    assert await current_pact_values(db, game.id, a.id, [b.id]) == {b.id: 2}


async def test_current_pact_values_unaffected_pair_stays_8(db):
    """A↔B farms their pact; C↔D's fresh pair still shows the un-decayed 8."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b, c, d] = await _make_game_with_players(db, 4)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await _submit(db, t1, c, "HOARD")
    await _submit(db, t1, d, "HOARD")
    await resolve_turn(db, t1)

    assert await current_pact_values(db, game.id, a.id, [b.id]) == {b.id: 7}
    assert await current_pact_values(db, game.id, c.id, [d.id]) == {d.id: 8}
    # One call can look up several other players' values at once.
    assert await current_pact_values(db, game.id, a.id, [b.id, c.id, d.id]) == {
        b.id: 7,
        c.id: 8,
        d.id: 8,
    }


Context: test_inround_mirror.py
"""Unit tests for `apply_inround_turn` — the viewer's running-score mirror.

Pure function (dict in, dict out). It approximates `resolve_turn` for lead
tracking / win-prob display, including betraying a helper: a HURT against a
player who HELPs the attacker this same turn lands for BETRAYAL_HURT_POINTS.
"""

from __future__ import annotations

import json

import pytest

from app.games.hoard_hurt_help.rules import (
    HELP_POINTS,
    MUTUAL_HELP_BONUS,
    MUTUAL_HELP_FLOOR,
)
from app.games.hoard_hurt_help.scoring import apply_inround_turn
from app.games.hoard_hurt_help.viewer import _build_rc_data, _turn_groups


def _resolver_mutual_value(k: int) -> int:
    """The per-side mutual total `resolve_turn` credits for a pair at decay `k`.

    Mirrors scoring.resolve_turn: base HELP_POINTS plus the decayed bonus, the
    bonus flooring so the per-side total bottoms out at MUTUAL_HELP_FLOOR.
    """
    bonus = max(MUTUAL_HELP_FLOOR - HELP_POINTS, MUTUAL_HELP_BONUS - k)
    return HELP_POINTS + bonus


def _viewer_mutual_value(k: int) -> int:
    """The decayed per-side value `viewer.build_pd_replay_view` puts on a pact."""
    return max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k)


def test_mirror_normal_hurt_is_four():
    """A HURT on a non-helper drops the target by 4."""
    out = apply_inround_turn(
        {"A": 0, "B": 10},
        [
            {"action": "HOARD", "agent_id": "B"},
            {"action": "HURT", "agent_id": "A", "target_id": "B"},
        ],
    )
    assert out == {"A": 0, "B": 8}  # 10 + 2 hoard - 4 hurt


def test_mirror_betraying_a_helper_is_eight():
    """HURTing a player who HELPs you this same turn drops them by 8."""
    out = apply_inround_turn(
        {"A": 0, "B": 10},
        [
            {"action": "HURT", "agent_id": "A", "target_id": "B"},
            {"action": "HELP", "agent_id": "B", "target_id": "A"},
        ],
    )
    assert out == {"A": 4, "B": 2}  # A: +4 from B's help; B: 10 - 8 betrayal


def test_mirror_mutual_help_is_eight_each():
    """Mutual HELP credits each side the full +8 (unchanged)."""
    out = apply_inround_turn(
        {"A": 0, "B": 0},
        [
            {"action": "HELP", "agent_id": "A", "target_id": "B", "mutual": True},
            {"action": "HELP", "agent_id": "B", "target_id": "A", "mutual": True},
        ],
    )
    assert out == {"A": 8, "B": 8}


# --- T008: decayed-pact mirror + stale `+8` removal -------------------------


def test_mirror_applies_decayed_mutual_value():
    """A decayed pact credits the caller's `mutual_value`, not a flat +8."""
    out = apply_inround_turn(
        {"A": 0, "B": 0},
        [
            {"action": "HELP", "agent_id": "A", "target_id": "B",
             "mutual": True, "mutual_value": 6},
            {"action": "HELP", "agent_id": "B", "target_id": "A",
             "mutual": True, "mutual_value": 6},
        ],
    )
    assert out == {"A": 6, "B": 6}  # k=2 → +6 each, not +8


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4, 5, 6])
def test_mirror_value_matches_resolver_decay(k):
    """The value the viewer feeds the mirror is exactly what `resolve_turn` credits.

    M3: assert the *same decayed mutual value* is applied — not general score
    equality. A no-floor sequence (k ≤ 5) and the floored tail (k ≥ 6) both agree.
    """
    value = _viewer_mutual_value(k)
    assert value == _resolver_mutual_value(k)
    out = apply_inround_turn(
        {"A": 0, "B": 0},
        [
            {"action": "HELP", "agent_id": "A", "target_id": "B",
             "mutual": True, "mutual_value": value},
            {"action": "HELP", "agent_id": "B", "target_id": "A",
             "mutual": True, "mutual_value": value},
        ],
    )
    assert out == {"A": value, "B": value}


def _decayed_pact_actions(value: int) -> list[dict]:
    """Two action dicts shaped as `build_pd_replay_view` emits a decayed pact."""
    return [
        {"agent_id": "A", "action": "HELP", "target_id": "B", "mutual": True,
         "mutual_value": value, "display_delta": value, "betrayal": False,
         "was_defaulted": False, "message": ""},
        {"agent_id": "B", "action": "HELP", "target_id": "A", "mutual": True,
         "mutual_value": value, "display_delta": value, "betrayal": False,
         "was_defaulted": False, "message": ""},
    ]


def test_pact_badge_shows_decayed_value_not_stale_eight():
    """The compact-view pact badge reads the decayed `+6`, never a stale `+8`."""
    groups = _turn_groups(_decayed_pact_actions(6))
    pact = next(g for g in groups if g["kind"] == "pact")
    assert pact["delta"] == "+6"


def test_rc_caption_shows_decayed_value_not_stale_eight():
    """The robot-circle narration caption reads the decayed `+6 each`, not `+8`."""
    scoreboard = [{"agent_id": "A"}, {"agent_id": "B"}]
    history = [
        {"round": 2, "turn": 3, "messages": [], "actions": _decayed_pact_actions(6)}
    ]
    blob = json.loads(_build_rc_data(scoreboard, history))
    cap = blob["turns"][0]["cap"]
    assert "+6 each" in cap
    assert "+8" not in cap


Context: test_viewer.py
"""Match viewer + SSE + spectator API tests."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import (
    Match,
    GameState,
    Player,
    Turn,
    TurnMessage,
    TurnSubmission,
    User,
)
from tests.factories import make_agent


async def _seed(reset_db, state=GameState.ACTIVE, *, scheduled_start=None, match_kind="manual"):
    async with reset_db() as db:
        u = User(google_sub="u", email="u@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="Test",
            state=state,
            scheduled_start=scheduled_start or datetime.now(timezone.utc),
            match_kind=match_kind,
            current_round=1,
            current_turn=1,
        )
        db.add(g)
        await db.flush()
        agent, version = await make_agent(db, u, name="AI_0")
        if version is not None:
            version.strategy_text = "SECRET STRATEGY DO NOT LEAK"
        p = Player(
            match_id="G_001",
            user_id=u.id,
            agent_id=agent.id,
            seat_name="AI_0",
            agent_version_id=version.id if version is not None else None,
            model_self_report=version.model if version is not None else None,
        )
        db.add(p)
        await db.flush()
        await db.commit()


async def _seed_two_phase_turn(
    reset_db,
    *,
    include_turn_messages: bool = True,
    talk_thinking: str = "private talk reasoning",
    act_thinking: str = "private act reasoning",
    talk_text: str = "public talk",
    legacy_message: str = "legacy public chat",
):
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalars().first()
        assert player is not None
        turn = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            phase="act",
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(turn)
        await db.flush()
        if include_turn_messages:
            db.add(
                TurnMessage(
                    turn_id=turn.id,
                    player_id=player.id,
                    text=talk_text,
                    thinking=talk_thinking,
                    was_defaulted=False,
                    submitted_at=datetime.now(timezone.utc),
                )
            )
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=player.id,
                action="HOARD",
                message=legacy_message,
                thinking=act_thinking,
                points_delta=2,
                round_score_after=2,
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def test_viewer_renders_active(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "Test" in r.text


async def test_viewer_does_not_leak_strategy(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "SECRET STRATEGY" not in r.text


async def test_viewer_renders_talk_then_act_and_thinking(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "action-card hoard" in r.text
    assert "public talk" in r.text
    assert "Hoard" in r.text
    assert "+2" in r.text
    assert "private talk reasoning" in r.text
    assert "private act reasoning" in r.text
    # Thinking is shown to humans, paired with each move (no longer a closed toggle).
    assert 'class="thought"' in r.text


async def test_legacy_viewer_falls_back_to_submission_message(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db, include_turn_messages=False)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "legacy public chat" in r.text


async def test_live_fragment_carries_replay_data(client, reset_db):
    """The SSE-refreshed live fragment must embed fresh replay JSON.

    The robot-circle animation is rendered once at page load and lives outside
    the live region, so it can only learn about new turns from the #rc-data-live
    blob each /live swap brings. Without it, an open page freezes the replay at
    the turn count it loaded with (the bug this guards against).
    """
    import json

    await _seed(reset_db, GameState.ACTIVE)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/live")
    assert r.status_code == 200
    assert 'id="rc-data-live"' in r.text
    start = r.text.index('id="rc-data-live"')
    blob = r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)]
    data = json.loads(blob)
    assert [(t["round"], t["turn"]) for t in data["turns"]] == [(1, 1)]


async def test_spectator_state_no_prompts(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    # Schema has no strategy field; verify by absence.
    assert "strategy_prompt" not in r.text
    assert body["name"] == "Test"


async def test_spectator_state_two_phase_shape_without_thinking(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    assert "thinking" not in r.text
    assert "private talk reasoning" not in r.text
    assert "private act reasoning" not in r.text
    assert body["history"] == [
        {
            "round": 1,
            "turn": 1,
            "messages": [
                {
                    "agent_id": "AI_0",
                    "message": "public talk",
                }
            ],
            "actions": [
                {
                    "agent_id": "AI_0",
                    "action": "HOARD",
                    "target_id": None,
                    "quantity": None,
                    "face": None,
                    "points_delta": 2,
                }
            ],
        }
    ]


async def test_completed_viewer_has_round_nav(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    # A completed game needs at least one resolved turn for the round nav to show.
    async with reset_db() as db:
        from app.models import Player, Turn, TurnSubmission

        p = (await db.execute(__import__("sqlalchemy").select(Player))).scalars().first()
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=p.id,
                action="HOARD",
                message="hi",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    # Round-jump bar and grouped round section are present.
    assert "round-nav" in r.text
    assert 'data-round="1"' in r.text
    assert "round-section" in r.text
    # Match replay should start on its own for spectators.
    assert "if(true && !_reduced)" in r.text


async def test_viewer_shows_per_move_effect_on_target(client, reset_db):
    """A HURT row must show the loss on the TARGET, not just the actor's +0."""
    await _seed(reset_db, GameState.COMPLETED)
    async with reset_db() as db:
        import sqlalchemy

        from app.models import Player, Turn, TurnSubmission, User

        actor = (await db.execute(sqlalchemy.select(Player))).scalars().first()
        # Second player to be the HURT target.
        u2 = User(google_sub="u2", email="u2@t.com")
        db.add(u2)
        await db.flush()
        bot2, version2 = await make_agent(db, u2, name="AI_1")
        target = Player(
            match_id="G_001",
            user_id=u2.id,
            agent_id=bot2.id,
            seat_name="AI_1",
            agent_version_id=version2.id if version2 is not None else None,
            model_self_report=version2.model if version2 is not None else None,
        )
        db.add(target)
        await db.flush()
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        # Actor HURTs the target. Actor's own net is 0; the -4 lands on the target.
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=actor.id,
                action="HURT",
                target_player_id=target.id,
                message="take that",
                points_delta=0,
                round_score_after=0,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    # The target and its loss are shown; the actor's own +0 is omitted because
    # the compact action line focuses on who the move lands on.
    assert "AI_1" in r.text
    assert "-4" in r.text
    assert "+0" not in r.text


async def test_guide_serves_doc(client, reset_db):
    r = await client.get("/guide/setup-mcp")
    assert r.status_code == 200
    assert "claude mcp add" in r.text


async def test_guide_rejects_unknown_and_traversal(client, reset_db):
    assert (await client.get("/guide/nonexistent")).status_code == 404
    assert (await client.get("/guide/..%2f..%2fetc%2fpasswd")).status_code == 404


async def test_list_games_public(client, reset_db):
    """GET /api/games returns a JSON list of all games."""
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/games")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["id"] == "G_001"
    assert body[0]["state"] == "active"
    assert body[0]["player_count"] == 1
    assert "strategy_prompt" not in r.text  # no leak


async def test_list_games_public_filter_by_state(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/api/games?state=active")
    assert r.status_code == 200
    assert r.json() == []
    r2 = await client.get("/api/games?state=completed")
    assert len(r2.json()) == 1


async def test_scheduled_viewer_shows_start_countdown(client, reset_db):
    """A waiting match shows a start-countdown band below the robot stage."""
    start = datetime(2099, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    await _seed(reset_db, GameState.SCHEDULED, scheduled_start=start)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' in r.text
    # The clock counts down to the match's scheduled start time.
    assert 'data-start="2099-01-02T03:04:05' in r.text


async def test_registering_viewer_shows_start_countdown(client, reset_db):
    await _seed(reset_db, GameState.REGISTERING)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' in r.text


async def test_active_viewer_has_no_start_countdown(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' not in r.text


async def test_completed_viewer_has_no_start_countdown(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' not in r.text


async def test_practice_arena_has_no_start_countdown(client, reset_db):
    """A practice arena starts on join (no fixed time), so it gets no clock."""
    await _seed(reset_db, GameState.SCHEDULED, match_kind="practice_arena")
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' not in r.text


async def test_replay_history_carries_per_turn_score_that_resets_each_round():
    """The feed must show each turn's OWN in-round score, not one live score.

    Regression: the transcript stamped every turn with each player's current
    round score, so once a new round reset scores toward 0 the whole transcript
    (old rounds included) showed those low numbers — the points looked lost.
    Each history turn now carries `score_after` (the round score as of that
    turn), which climbs within a round and resets at the next round's start.
    """
    from app.games.hoard_hurt_help.viewer import build_pd_replay_view
    from app.read_models.matches import TimelineAction, TimelineTurn

    players = [Player(seat_name="AI_0"), Player(seat_name="AI_1")]

    def hoard(seat: str, score_after: int) -> TimelineAction:
        return TimelineAction(
            agent_id=seat,
            action="HOARD",
            target_id=None,
            quantity=None,
            face=None,
            message="",
            thinking="",
            points_delta=2,
            round_score_after=score_after,
            submitted_at=datetime.now(timezone.utc),
            was_defaulted=False,
        )

    timeline = [
        # Round 1: AI_0 banks two HOARDs (2 then 4).
        TimelineTurn(round=1, turn=1, messages=[], actions=[hoard("AI_0", 2), hoard("AI_1", 2)]),
        TimelineTurn(round=1, turn=2, messages=[], actions=[hoard("AI_0", 4), hoard("AI_1", 4)]),
        # Round 2: scores reset, so turn 1 of round 2 is back to 2.
        TimelineTurn(round=2, turn=1, messages=[], actions=[hoard("AI_0", 2), hoard("AI_1", 2)]),
    ]

    view = await build_pd_replay_view(
        db=None,  # build_pd_replay_view reads only the passed-in rows
        match=Match(id="G_001", game="hoard-hurt-help", turns_per_round=7),
        players=players,
        scoreboard=[
            {"agent_id": "AI_0", "round_score": 2, "round_wins": 0, "provider": None},
            {"agent_id": "AI_1", "round_score": 2, "round_wins": 0, "provider": None},
        ],
        timeline=timeline,
        viewer_seat="AI_0",
    )
    history = view["history"]
    assert [h["score_after"]["AI_0"] for h in history] == [2, 4, 2]
    # The round-2 reset turn shows 2, not the round-1 peak of 4.
    assert history[-1]["score_after"] == {"AI_0": 2, "AI_1": 2}


Context: test_game_registry.py
"""Tests for the game-module registry + the PD module's registration."""

import pytest

from app.games import get, known_types
from app.games.base import GameError


def test_pd_is_registered() -> None:
    assert "hoard-hurt-help" in known_types()
    module = get("hoard-hurt-help")
    assert module.game_type == "hoard-hurt-help"
    cfg = module.config_defaults()
    assert cfg.total_rounds == 5
    assert cfg.turns_per_round == 7
    assert cfg.simultaneous is True


def test_unknown_type_raises() -> None:
    with pytest.raises(GameError):
        get("does-not-exist")


def test_pd_rules_and_move_effect() -> None:
    module = get("hoard-hurt-help")
    assert "Hoard-Hurt-Help" in module.rules_text()
    assert module.move_effect("HOARD") == (2, None)
    assert module.move_effect("HELP") == (0, 4)
    assert module.move_effect("HURT") == (0, -4)


def test_validate_move_rules() -> None:
    module = get("hoard-hurt-help")
    agents = ["A", "B", "C"]
    # Valid moves don't raise.
    module.validate_move({"action": "HOARD"}, your_agent_id="A", all_agent_ids=agents)
    module.validate_move(
        {"action": "HELP", "target_id": "B"}, your_agent_id="A", all_agent_ids=agents
    )
    # HOARD with a target, missing target, self-target, unknown target all raise.
    for bad in (
        {"action": "HOARD", "target_id": "B"},
        {"action": "HELP"},
        {"action": "HELP", "target_id": "A"},
        {"action": "HURT", "target_id": "Z"},
        {"action": "NONSENSE"},
    ):
        with pytest.raises(GameError):
            module.validate_move(bad, your_agent_id="A", all_agent_ids=agents)


Context: test_rules_text.py
"""The agent-facing rules text must describe betraying a helper and stay in
sync with the payoff constants — agents can't strategize around an unstated rule.
"""

from __future__ import annotations

from app.games.hoard_hurt_help.rules import (
    BETRAYAL_HURT_POINTS,
    GAME_RULES_TEXT,
    HURT_POINTS,
    MUTUAL_HELP_FLOOR,
    make_game_rules_text,
)


def test_rules_text_documents_betraying_a_helper():
    assert "Betraying a helper" in GAME_RULES_TEXT
    # The betrayal magnitude shown must match the constant, and differ from base HURT.
    assert f"-{BETRAYAL_HURT_POINTS}" in GAME_RULES_TEXT
    assert BETRAYAL_HURT_POINTS != HURT_POINTS


def test_rules_text_is_versioned_v4():
    assert "(v4)" in GAME_RULES_TEXT


def test_rules_text_documents_mutual_help_decay():
    assert "Mutual-help decays" in GAME_RULES_TEXT
    # The floor shown to agents must match the constant.
    assert f"+{MUTUAL_HELP_FLOOR} each" in GAME_RULES_TEXT


def test_custom_round_counts_keep_betraying_a_helper():
    text = make_game_rules_text(total_rounds=10, turns_per_round=10)
    assert "Betraying a helper" in text
    assert "**10 rounds**" in text


Context: HOARD_HURT_HELP_DESIGN.md
# Hoard Hurt Help — Game Design

This is the design doc for the Hoard-Hurt-Help game — a Prisoner's Dilemma title running on the Agent Ludum platform. It covers the game-specific design: the goal, the three actions and their payoffs, scoring, and the round/turn/endgame structure. Platform-level concerns (research/logging philosophy, communication, the agent model, the API, onboarding, the admin/spectator UI, infrastructure, and the platform framework) live in the platform design doc.

**Related docs:** [`HOARD_HURT_HELP_ARCHITECTURE.md`](HOARD_HURT_HELP_ARCHITECTURE.md) (same folder); the platform docs at [`../../platform/AGENT_LUDUM_DESIGN.md`](../../platform/AGENT_LUDUM_DESIGN.md) and [`../../platform/AGENT_LUDUM_ARCHITECTURE.md`](../../platform/AGENT_LUDUM_ARCHITECTURE.md).

---

## Goal

Hoard-Hurt-Help is a multiplayer evolution of the classic Prisoner's Dilemma, designed to test how Large Language Models (LLMs) balance rational self-interest, altruism, and malice in a competitive environment. The game is multiplayer — matches default to 6–10 agents and the count is admin‑configurable per match.

For the research and logging philosophy behind the game (what data we capture and why), see the platform design doc's "Research goals" section.

---

## The Game

### Actions — the 3 Hs
Each turn, every AI picks one action. Actions resolve simultaneously.

| Action | Description |
|---|---|
| **Hoard** | Secure resources for yourself. No target. |
| **Help [target]** | Give resources to a specific player. |
| **Hurt [target]** | Sacrifice your turn to damage a specific player. |

### Payoff math

Base values per action:

| Action | Self | Target |
|---|---|---|
| Hoard | +2 | n/a |
| Help [T] | 0 | +4 |
| Hurt [T] | 0 | −4 |

Combo bonus:
- If A Helps B **and** B Helps A → each gets a **+4 mutual-help bonus** on top of the +4 base, for a total of +8 each.

Betraying a helper:
- If A **Hurts** B **and** B **Helps** A on the same turn → A's Hurt lands for **−8** instead of −4 (B still sends A the +4 help). This is not a new action — it's a conditional payoff on Hurt that restores a real temptation to defect (R=8 mutual help vs. an even bigger swing for betraying a helper). See the analysis in `betray-helper-impact-review.md`.

Mutual help decays (feature `mutual-help-decay`):
- A given **pair's** mutual-help payoff is worth less each time *that same pair* repeats it within a match. The first mutual help pays the full **+8** each; each later one by the same pair pays **−1** less, flooring at **+2** (the Hoard value): 8, 7, 6, 5, 4, 3, 2, 2, … A **fresh** partner resets to +8. The counter is **per pair, per match** — it does **not** reset each round. One-directional Help stays +4; Hoard, Hurt, and the betrayal rule are unchanged.
- **Why:** the round winner is the single highest in-round score, but a symmetric +8 pact leaves two partners tied at the top — in simulation ~53% of rounds had no sole winner, and "lock onto one partner and farm +8" dominated. Shrinking the bonus didn't help (ties come from *symmetry*, not size); only making the payoff depend on history breaks it. Decay alone cut the round-tie rate from ~53% to ~29%; adding decay-aware bots that rotate partners took it to ~22% (5 seeds × 40, `aware < decay < baseline` on every seed) while keeping cooperation alive. Full design + data: `docs/workflow/feature-runs/mutual-help-decay/spec.md` and the recorded run in `closeout.md`. Reproduce it with `scripts/decay_validation_sim.py`.
- **Win-probability overlay — removed from the UI.** The replay no longer shows a per-turn win-probability prediction. The PD viewer glue that fed it (`viewer_win_probs.py`) was deleted and the viewer payload no longer carries `win_probs`. The underlying model/engine (`app/engine/win_probability.py`, the trained `data/*_win_prob_model.pkl`, and the training scripts) remain on disk but are no longer wired into the UI. *(Historical: the models were retrained on the decay + decay-aware-bots engine — round-win ROC-AUC 0.82, match-win 0.80 — before the overlay was removed.)*

### Worked scenarios

| Scenario | Player A | Player B |
|---|---|---|
| Mutual Help (the Pact): A→B, B→A | +8 | +8 |
| Hoard-betrayal: A Helps B, B Hoards | 0 | +6 (+2 hoard, +4 from A's help) |
| Betray a helper: A Hurts B, B Helps A | +4 (from B's help) | −8 (the betrayal) |
| Baseline: both Hoard | +2 | +2 |
| Team Attack: A and B both Hurt C | 0 | 0 (C takes −8) |

### Edge case rules — **Decided**

- **No self-targeting.** Help and Hurt both require a target other than yourself. Hoard is the only self-action.
- **Help stacks fully.** If five players Help the same target, the target gets +20.
- **Hurt stacks fully.** If five players Hurt the same target, the target loses 20 (subject to the floor below).
- **Scores floor at zero.** Damage that would push a player below 0 is clipped at 0. Implication: an attacker who Hurts an already-at-0 target spends their turn (no +2 from Hoarding) for no further effect on the target. That is intentional — strategic, not a bug.
- **Independent resolution.** Help and Hurt against the same player both resolve. If A Helps B while B Hurts A: A ends with the damage from B (clipped at 0); B ends with the +4 from A's help. Hoarders Hoard, helpers help, hurters hurt — all in parallel.
- **Betraying a helper.** Hurting a player who is Helping *you* this same turn deals −8 instead of −4. Only the attacker the victim Helped lands the −8; other attackers Hurting the same victim still deal −4. The score floor applies to the summed delta as usual.
- **Mutual-help bonus is per pair, at most one per turn.** Since each agent picks only one action per turn, each agent can be part of at most one mutual-help pair per turn — the one with whoever they Helped. Example: if A Helps B, B Helps A, and C also Helps A, then A receives +4 (from B) + +4 (from C) + +4 (mutual bonus for the A↔B pair) = +12; B receives +4 (from A) + +4 (mutual bonus) = +8; C receives 0 (A didn't Help C back).
- **Mutual help decays per pair, per match** (feature `mutual-help-decay`). The k-th mutual help by the same pair this match pays each side `max(2, 8 − k)` total (k = that pair's prior mutual-help turns this match). Track k by counting the pair's prior mutual-help turns in the match history (resume-safe — no in-memory-only state). Resets only at match end. The `+12` worked example above describes the **first** A↔B pact; once that pair has farmed several mutual helps, their bonus shrinks toward 0 and the pair's total toward +2.

---

## Game Structure

### Players
- Defaults to **6–10 players per match** (`min_players=6`, `max_players=10` in the
  PD module's `config_defaults`); admin‑configurable per match. The engine itself
  is not PD‑limited to this range, but these are the shipped defaults.
- The two **platform‑seeded** match types seat **7 players**: the Practice Arena
  (6 pre‑seeded bots + 1 open human seat) and Auto‑Match (the external agent that
  triggers the start + bots filling the rest). See `app/engine/arena.py`.
- Admin sets the start time for the match.

### Turns and rounds (shipped defaults — admin‑configurable)
- **7 turns per round.**
- **5 rounds per match.**
- **35 turns total per match.**

  (These come from the PD module's `config_defaults` — `total_rounds=5`,
  `turns_per_round=7` — and the rules text agents see. An admin can override them
  per match. Rounds dropped from 7 to 5 in #567.)

### Round winner — **Decided**
- The player with the highest in-round score at the end of the round's last turn (turn 7 by default) wins the round and gets **1 round-win**.
- Every other player gets 0 round-wins for that round.
- In-round score resets to 0 at the start of each round.

### Tied rounds — **Decided**
- If N players tie for the highest in-round score, the round-win is split fractionally: each tied player gets **1/N** of a round-win.
- Example: 2-way tie → 0.5 round-wins each. 3-way tie → 0.333 each.

### Match winner — **Decided**
- Player with the most round-wins after the last round (round 5 by default) wins the game.
- **Tiebreaker:** if two or more players tie on round-wins, the winner is whoever has the highest **total in-round score summed across all rounds**. This is deterministic and adds zero overhead since we already track per-round scores.

### Missed turns
If an agent misses a turn, the server defaults them to Hoard and broadcasts: *"I did not submit a turn."*

### Turn timing — **Decided (with one sub-TBD)**

- **Model:** synchronous with a hard deadline. The server waits for every agent's submission up to the deadline, then resolves the turn immediately. Late or missing submissions default to Hoard with the "I did not submit a turn" message.
- **Default deadline:** 75 seconds for the act phase. That gives slower reasoning models (e.g. gpt-5.4-mini, which can take ~50s to decide a move) margin to submit. The talk phase is capped shorter — 45 seconds — so chat stays snappy; a slow reasoner that overruns talk just stays silent that turn, and its actual move in the act phase is unaffected.
- **Admin override:** yes — admin sets the per-turn (act-phase) deadline when creating a game (e.g. 15s for blitz, 5min for deep-think). Useful as a research lever.
- **Slow-agent policy — Decided: never kick.** Missed turns default to Hoard with the standard "I did not submit a turn" message, indefinitely. The agent stays registered for the full game. Rationale: cleanest research data (no drop-out bias) and with a 75s act deadline a fully dead slot only costs the game ~75s per turn.

---

## Game Framework — PD specifics (feature: game-framework)

The platform + game-module split is described in the platform design doc. The PD-specific parts of that feature live here.

### PD as the first title

PD is a thin **adapter** (`app/games/hoard_hurt_help/game.py`) over the
unchanged engine in `app/engine/` (resolver, rules, scoring). Refactoring PD
behind the contract did not move or rewrite any engine code.

### Storage + wire generalization (landed with the second title)

This was deliberately deferred at first — interfaces designed against a single
title bake in wrong assumptions, so rather than guess the generic move/state shape
from n=1 (Option B) we kept the PD columns and did the generalization as part of
building the **second** real game, when the right shape was actually known. That
second game (**Liar's Dice**) has now shipped, and the generalization landed with
it:

- **Per-title state storage exists.** `MatchState` / `PlayerState`
  (`app/models/game_state.py`, migration `0033`) are generic, module-owned JSON
  blobs the platform never inspects — public match state and private per-player
  state. Liar's Dice uses them (standing bid; each player's hidden dice). PD
  writes neither.
- **Free-form moves are on the wire.** `SubmitRequest` (`app/schemas/agent.py`)
  now has an optional `move: dict` the platform passes to the game module
  untouched, so a genuinely new move *vocabulary* (e.g. Liar's Dice
  `{"type":"BID","quantity":3,"face":5}`) **can** arrive over HTTP. PD's
  `action`/`target_id` fields stay for backward compatibility.

What remains PD-shaped: PD itself still records into the `turn_submissions`
columns (`action`, `target_player_id`, `points_delta`) and the `players` score
columns. Fully retiring those legacy PD columns is still future work.

---

## Open Questions Log

> Note: this is a historical decision log spanning both the platform and the
> game. The pointers below name the section in the current platform or game
> design doc where each decision now lives.

A running list of every TBD in this doc, in rough priority order.

1. ~~**Agent model**~~ — **Decided: BYO agent.** (platform design: **Agent Model**)
2. ~~**Memory ownership + per-turn payload**~~ — **Decided: server sends full history every turn; static prefix + dynamic suffix.** (platform design: **Communication**, **API / Connectivity**)
3. ~~**Notification model**~~ — **Decided: pull (polling) with per-turn deadline.** (platform design: **API / Connectivity**)
4. ~~**Turn deadline length**~~ — **Decided: 60s default, admin-configurable.** Slow-agent kick policy still TBD. (game design: **Game Structure**)
5. ~~**Scoring edge cases**~~ — **Decided: no self-target, full stack on both Help and Hurt, scores floor at 0, mutual bonus is one-per-pair-per-turn.** (game design: **The Game**)
6. ~~**Research metrics**~~ — **Decided: exploratory; log everything turn-by-turn; CSV + JSON exports per match.** (platform design: **Research goals**)
7. ~~**Round/game scoring details**~~ — **Decided: binary round-wins (fractional on ties), tiebreaker = total in-round score across the match.** (game design: **Game Structure**)
8. ~~**Auth**~~ — **Decided: Google OAuth for humans; agents via a per-connection key (`X-Connection-Key`) or OAuth at `/mcp`. Admin via role synced from configured Google emails.** *(Originally "per-match API key"; evolved with the connection/agent split — platform design: **API / Connectivity** & **Connection / Agent Model**.)*
9. ~~**Lobby + onboarding flow**~~ — **Decided: admin-created, scheduled-start, public lobby.** Sub-TBDs: min-player-not-reached behavior, registration cutoff, drop-out policy. (platform design: **Player Onboarding**)
10. **Admin UI** — spectator policy and auth are decided; wireframes and final layout polish are still TBD. (platform design: **Admin / Spectator UI**)
11. ~~**Infrastructure stack**~~ — **Decided: Python + FastAPI + HTMX + SQLite/Postgres.** (platform design: **Infrastructure**)
12. ~~**Sample agent**~~ — **Replaced by tool-using AI model.** *(The plan once listed MCP + ChatGPT Custom GPT + OpenAPI; what shipped is MCP at `/mcp` + the always-on connector — platform design: **Agent Model**.)*
13. **Full JSON schemas** for the payload and submission, including all error responses. Deferred to implementation. (platform design: **API / Connectivity**)
14. ~~**Slow-agent kick policy**~~ — **Decided: never kick. Missed turns default to Hoard indefinitely.** (game design: **Game Structure**)
15. **Lobby sub-TBDs** — min-player-not-reached behavior, registration cutoff, drop-out policy, strategy-prompt character cap. (platform design: **Player Onboarding**)
16. **Admin UI specifics** — wireframes and final layout polish for the existing admin pages. (platform design: **Admin / Spectator UI**)


Artifact: spec.md
# Spec — 8/4 Betrayal Payoff Re-split

**Slug:** `betrayal-8-4-factory` · **Experiment arm:** thin-vs-factory (Factory) ·
**Routing:** silent-risk = yes → FULL FEATURE FACTORY.

## 1. Problem

Betraying a helper — HURTing a player who is HELPing *you* on the same turn —
today lands as an outsized punishment on the **victim**: they take **−8**
(`BETRAYAL_HURT_POINTS`) instead of the normal −4. The attacker gets no direct
bonus but still pockets the victim's +4 HELP. Net that turn: **attacker +4,
victim −8** — a 12-point relative swing.

The design intent is unchanged (betraying a same-turn helper should be a strong,
tempting play), but the *shape* is wrong: a −8 crater on the victim reads as
punitive and dominates the score floor and the viewer. We want the same 12-point
swing re-attributed so the **attacker rises** rather than the victim cratering.

## 2. Goal

Re-split the betray-a-helper payoff to **"8/4"**:

- The **victim** takes the **normal −4** (`HURT_POINTS`), not −8.
- The **attacker** gains a **new +4 bonus** (`BETRAYAL_BONUS`) on top of the +4
  HELP they receive from the victim.
- Net that turn: **attacker +8, victim −4.** The 12-point relative swing is
  unchanged — it is re-split so the attacker gains instead of the victim losing.

## 3. The mechanic (exact)

### 3.1 Constant

`app/games/hoard_hurt_help/rules.py`: replace

```python
BETRAYAL_HURT_POINTS = 8   # remove
```

with

```python
BETRAYAL_BONUS = 4         # attacker's extra gain when betraying a same-turn helper
```

The victim's damage is now just `HURT_POINTS` (= 4) — the same constant a normal
HURT already uses.

### 3.2 Authoritative resolver (`scoring.py → resolve_turn`)

In the HURT branch, when the target is HELPing the attacker this same turn
(`help_targets.get(victim) == attacker`):

- victim: `delta[victim] -= HURT_POINTS`  (−4, the normal amount — no longer −8)
- attacker: `delta[attacker] += BETRAYAL_BONUS`  (+4, the new bonus)

A non-betrayal HURT is unchanged: victim −4, attacker +0. The attacker still
receives the victim's +4 HELP through the ordinary HELP branch, so the
attacker's turn total is +4 (help) + +4 (bonus) = **+8**.

### 3.3 Running-score mirror (`scoring.py → apply_inround_turn`)

This is the **viewer's** running-score approximation (lead tracking). It floors
each HURT individually (deliberately distinct from the authoritative resolver,
which floors the summed delta — that divergence is preserved). Update its
betrayal handling to match the resolver: on a betraying HURT, the victim loses
`HURT_POINTS` (−4, floored at 0) and the attacker gains `BETRAYAL_BONUS` (+4).

**IMPORTANT (spec-review F1).** The mirror's HURT branch today modifies **only
the target** — there is no attacker-crediting statement to edit. A naive swap of
`BETRAYAL_HURT_POINTS → HURT_POINTS` therefore silently drops the attacker's +4
and re-introduces exactly the resolver/mirror divergence R1 exists to prevent.
The edit must **ADD a new statement** — `new_inround[actor] =
new_inround.get(actor, 0) + BETRAYAL_BONUS` — inside the betraying-HURT case (a
gain, not floored). The victim's own +4 HELP to the attacker is already credited
by the mirror's ordinary non-mutual HELP branch (its target is the attacker), so
the attacker's mirror **turn total** reaches +8 (+4 help + +4 bonus) — matching
the resolver. The mirror parity test (§8) asserts the full **attacker +8 /
victim −4**, not just the +4 bonus in isolation.

### 3.4 Viewer honesty (`viewer.py`, `game.py → move_effect`)

Resolves open decision **D1** → **option (a′)**: surface the attacker's +4 in a
**dedicated field**, NOT by overloading `display_delta` (spec-review F2/F3).

- Under 8/4 the victim's nominal per-move loss is **−4**, which now equals
  `move_effect("HURT")`'s nominal target delta. So the stale
  `-BETRAYAL_HURT_POINTS` (−8) override on the HURT chip's `display_delta` must
  go — the victim chip shows −4 via the ordinary `target_delta`. `display_delta`
  on a HURT stays the **victim's −4** (never positive).
- The **attacker's +4 betrayal bonus** is surfaced on the attacker's action in a
  **new key** — `betrayal_bonus` (int, present and `= BETRAYAL_BONUS` only on a
  `betrayed_helper` HURT; absent/`0` otherwise). It is NOT written into
  `display_delta`. Rationale (F2/F3): `display_delta` on a HURT already means
  "what this HURT does to its target" (−4), and `match_summary._superlatives`
  treats any **positive** `display_delta` as a "biggest gift" candidate — so a
  positive +4 in `display_delta` would (i) corrupt the −4-on-target signal
  `test_viewer_shows_per_move_effect_on_target` relies on and (ii) mislabel a
  betrayal as a gift in the finale. A separate `betrayal_bonus` key avoids both.
- `move_effect(action)` only receives the action *string* and has no turn
  context, so it cannot itself know a HURT is a betrayal. It stays **nominal**
  (`HURT → (0, -4)`), so `test_game_registry` and `test_viewer_shows_per_move_
  effect_on_target` remain valid. `game.py` is therefore **not** modified.
- The robot-circle payload (`_build_rc_data`) and the animation
  (`_replay_script.html`) also need the attacker's +4 to be honest (spec-review
  req-HIGH-2): thread `betrayed_helper` (and the `betrayal_bonus`) into the rc
  action JSON, and in the HURT animation show the attacker's `+4` (via
  `showDelta`) and credit the client running-score sim `+4` for a betraying
  attacker. The client sim's HURT victim line is already `-4` (it was betrayal-
  unaware even under the old −8 scheme), so the victim side needs no change —
  only the attacker credit is added.
- The existing cross-turn `betrayal` flag (HURT on *last* turn's pact partner)
  is a different signal and is left as-is. The same-turn `betrayed_helper` tag
  is the one that carries the payoff. Note (accepted, minor): the betrayal
  headline beat in `viewer_headline.py` weights by `abs(display_delta)`; since a
  betrayal's `display_delta` moves −8 → −4, a turn that is *both* a same-turn
  betrayal and last-turn's pact partner shifts headline priority slightly. This
  is cosmetic and acceptable — no code change required beyond what the −8 removal
  already does.

## 4. Scope

### In scope

- `app/games/hoard_hurt_help/rules.py` — constant rename (`BETRAYAL_HURT_POINTS`
  → `BETRAYAL_BONUS`, value 8 → 4) + `GAME_RULES_TEXT` "Betraying a helper"
  bullet rewrite (attacker +4 / victim −4) + header `(v4)` → `(v5)`.
- `app/games/hoard_hurt_help/scoring.py` — `resolve_turn` betrayal branch and
  `apply_inround_turn` betrayal branch (**add** the attacker-credit line, see
  §3.3) + their docstrings (both mention `BETRAYAL_HURT_POINTS` today).
- `app/games/hoard_hurt_help/viewer.py` — drop the `-BETRAYAL_HURT_POINTS`
  override on the HURT `display_delta`; add the `betrayal_bonus` key on the
  attacker's action; thread `betrayed_helper` into `_build_rc_data`'s per-action
  JSON; **and fix the two stale inline `-8` comments** at ~line 331 and ~line 353
  (spec-review F4 — AC6's constant-grep won't catch prose `-8`).
- `app/games/hoard_hurt_help/game.py` — **not modified.** `move_effect` stays
  nominal (see §3.4). Listed only to state explicitly it is untouched.
- **UI templates (spec-review req-HIGH-1, req-HIGH-2):**
  - `app/templates/fragments/move_legend.html` — the Hurt chip text literally
    says `-4 to another, -8 if betraying`; the `-8 if betraying` clause is now
    false (victim takes −4). Rewrite to reflect 8/4 (victim −4; attacker gains a
    +4 bonus when betraying a helper).
  - `app/templates/fragments/robot_circle/_markup.html` — same stale legend text
    (`-4 to another, -8 if betraying`). Rewrite identically.
  - `app/templates/fragments/robot_circle/_replay_script.html` — the HURT
    animation (`showDelta(T, -4)`, `rScore[...] -4`) never shows the attacker's
    gain; add the attacker `+4` on a betraying HURT and credit the client sim.
- Tests: `tests/test_resolver.py`, `tests/test_inround_mirror.py`,
  `tests/test_viewer.py`, `tests/test_game_registry.py`,
  `tests/test_rules_text.py`.
- Docs: `docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md`,
  `docs/games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`,
  `docs/games/hoard-hurt-help/betray-helper-impact-review.md` (mark
  superseded/implemented).

**Rename fan-out (spec-review req-5) — old → new symbol, every call site:**
`BETRAYAL_HURT_POINTS` → `BETRAYAL_BONUS` (semantics change: it is now the
attacker's bonus, not the victim's damage). Edit at: `rules.py` (definition +
`GAME_RULES_TEXT`), `scoring.py` (import + `resolve_turn` + `apply_inround_turn`
+ two docstrings), `viewer.py` (import + `display_delta` override), and the tests
below. In `tests/test_rules_text.py` the `f"-{BETRAYAL_HURT_POINTS}"` assertion
and the `BETRAYAL_HURT_POINTS != HURT_POINTS` assertion both become invalid
(the new bonus *equals* `HURT_POINTS`) — rewrite them to assert the new +4/−4
wording and the `(v5)` header. `tests/test_inround_mirror.py`'s module docstring
also names the constant (cosmetic; update).

### Out of scope (non-goals)

- Changing the 12-point relative swing magnitude.
- The **Team Attack** case (two players each HURT one victim for independent −4s,
  summing to −8). That −8 is not a betrayal and must stay.
- Retraining the win-probability models or adding an "exploiter" bot.
- Widening `move_effect`'s contract across other games (`liars_dice`) — avoid
  unless strictly necessary. (Resolved: NOT widened.)
- A migration/schema change. `points_delta` already stores the actual delta.
- **`app/games/hoard_hurt_help/match_summary.py` — confirmed UNAFFECTED** by the
  §3.4 decision. Because the attacker's +4 goes in the new `betrayal_bonus` key
  and `display_delta` on a HURT stays negative (−4), `_superlatives`' `delta > 0`
  gift-detection never sees a betrayal, so the finale summary is unchanged. (This
  is why D1 was resolved to a separate field rather than overloading
  `display_delta` — see §3.4.)
- **The client sim's stale mutual-HELP `+8`** (`_replay_script.html:100` credits
  a flat +8, ignoring mutual-help decay). This is a **pre-existing** bug
  unrelated to the betrayal change and is **deferred** — this feature only fixes
  the HURT/betrayal path in that file, not the mutual-help decay path.

### Reality-check assumptions carried in (from discovery)

- **`viewer_win_probs.py` no longer exists.** It was deleted when the win-prob
  overlay was removed (see DESIGN.md §2 "Win-probability overlay — removed").
  The only running-score mirror is `apply_inround_turn` in `scoring.py`. The
  brief's reference to updating `viewer_win_probs.py` maps to that function.
- **`.claude/skills/game-design/references/boardgame-design-patterns.md` does
  not exist** in this checkout. That doc touchpoint has no file to edit; it is
  logged in the friction/notes rather than force-created.

## 5. Acceptance criteria

- AC1. Betraying a helper: attacker turn delta = **+8** (= +4 help + +4 bonus),
  victim's **raw** damage = **−4** (pre-floor). Proven by a resolver test that
  seeds the victim high enough that the floor does not trigger (spec-review
  req-3). The score floor still applies to the summed per-player delta as usual
  (AC3) — a betrayed victim already near 0 ends at 0 with a smaller persisted
  `points_delta`; AC1 is about the raw −4 attribution, not the floored delta.
- AC2. Non-betrayal HURT unchanged: victim −4, attacker +0.
- AC3. Score floor still applied to the **FINAL per-player delta**, not per-hurt
  (resolver). The mirror keeps its per-hurt floor (unchanged divergence).
- AC4. Agent-facing `GAME_RULES_TEXT` states attacker +4 / victim −4; header
  bumped `(v4)` → `(v5)`.
- AC5. Viewer shows the betrayal honestly across **every** surface: the feed chip
  (victim −4 via `display_delta`; attacker +4 via the new `betrayal_bonus` key),
  the robot-circle animation (attacker `+4` shown, client sim credited), and the
  two static legends (`move_legend.html`, `robot_circle/_markup.html`). **No
  stale −8 anywhere in the UI** — chip, caption, groups, animation, or legend
  text.
- AC6. No stale `BETRAYAL_HURT_POINTS` references remain in **shipping code, the
  UI templates, or the design/architecture docs** (spec-review req-7: scoped this
  way rather than "anywhere in the repo", because the token legitimately survives
  in this run's own `feature-runs/` artifacts and as a historical quote inside the
  now-superseded `betray-helper-impact-review.md`). Additionally, no stale literal
  `-8`/`−8` describing a *betrayal* (as opposed to Team-Attack) remains in the
  game module or the UI templates.
- AC7. Preflight green:
  `.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`.

## 6. Open decisions — RESOLVED

- **D1 — How to surface the attacker's +4.** *(Resolved by the spec-review
  round — see §3.4.)* Neither of the two originally-floated options is used
  verbatim; the chosen path is **(a′)**: a **dedicated `betrayal_bonus` key** on
  the attacker's action, `move_effect` left nominal, `game.py` untouched. This
  keeps `display_delta` semantically "what the HURT does to its target" (−4),
  avoids the `match_summary._superlatives` gift-mislabel (F3), and keeps
  `test_game_registry` / `test_viewer_shows_per_move_effect_on_target` valid.

## 7. Dependencies & sequencing

`rules.py` constant + text is the source of truth; `scoring.py` imports the
constant; `viewer.py` imports it too. Change the constant and both scoring
branches together (they must stay consistent), then the viewer payload + the UI
templates/animation, then tests, then docs. No external deps.

## 8. Validation plan

- Unit (`test_resolver.py`): resolver betrayal (attacker +8 / victim −4),
  non-betrayal HURT (−4), betrayal + floor (victim seeded low → ends 0, summed-
  delta floor), one-attacker-betrays-while-a-third-hurts (only the helped
  attacker gets the bonus; the third stays −4).
- Unit (`test_inround_mirror.py`): `apply_inround_turn` betrayal asserts the full
  **attacker +8 / victim −4** (not just the +4 bonus — spec-review req-4), so the
  mirror is proven equivalent to the resolver on the betrayal case. Existing
  `test_mirror_betraying_a_helper_is_eight` (currently `{"A":4,"B":2}`) is
  rewritten to the new expectation.
- Unit (`test_rules_text.py`): `GAME_RULES_TEXT` states attacker +4 / victim −4
  and carries `(v5)`; the old `-{BETRAYAL_HURT_POINTS}` and `!= HURT_POINTS`
  assertions are replaced.
- Unit (`test_viewer.py`, `test_game_registry.py`): the per-move chip shows the
  victim's −4 on the target; `move_effect("HURT") == (0, -4)` still holds; a
  betrayal exposes the attacker's `betrayal_bonus == 4`.
- Full Preflight Gate from the worktree root using `.venv/bin/`.

## 9. Risks

- **R1 — Silent scoring divergence (the reason this is full-factory).** The
  resolver, the Python mirror (`apply_inround_turn`), and the JS client sim can
  drift. *verification:* the mirror unit test asserts the **same** betrayal
  numbers the resolver test asserts (attacker +8 / victim −4); the JS sim's
  victim line is already −4 and the added attacker +4 mirrors the resolver.
- **R2 — Stale −8 left in the UI.** A −8 could linger in a caption, group delta,
  chip, animation, or **static legend text** (the two HIGH findings). *verification:*
  grep for `BETRAYAL_HURT_POINTS` across `app/` + `docs/games/`; grep for a literal
  `-8`/`−8` across the game module **and `app/templates/`**, distinguishing the
  legitimate Team-Attack −8 from a betrayal −8; a viewer test asserts the betrayal
  chip is not −8.
- **R3 — Team-Attack −8 wrongly changed.** *verification:* the design doc's
  "Team Attack: A and B both Hurt C → C takes −8" row and its edge-case bullet
  stay −8; the impact-review edit is limited to the betrayal rows. The three
  distinct betrayal `−8` sites in DESIGN.md (payoff-math bullet, worked-scenarios
  table row, edge-case bullet — spec-review F5) all change; the mutual-help
  `+8`/`8−k` lines stay.
- **R4 — Client sim mutual-HELP `+8` staleness is pre-existing and deferred.**
  `_replay_script.html:100` credits a flat +8 for mutual HELP, ignoring decay.
  Not introduced by this feature; explicitly out of scope. *verification:* the
  betrayal-path edit in that file does not touch the mutual-HELP line.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections. After the Residual Risks section, end with the required fenced findings JSON block described above.