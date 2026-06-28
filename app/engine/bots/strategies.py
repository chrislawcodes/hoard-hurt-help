"""Strategy selection for bots.

The per-strategy plan *rules* live in :mod:`app.engine.bots.plan_rules` — a
registry of small rule functions, one per bot, built from shared row helpers.
This module owns the seeded "read the table" selectors those rules consume
(best partner, recent helper, runaway leader, the crowd-follower core, …) and
the thin :func:`choose_action_plan` that wires the two together. Keeping the
selectors here preserves their exact seeded-tiebreak behavior in their original
home and avoids an import cycle with the rules module.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Sequence

from app.engine.game_records import ActionRecord

from . import plan_rules
from .plan_rules import HOSTILE_TRUST
from .signals import TalkSignal
from .types import BotContext, BotPlan, BotProfile

STRATEGY_ALIASES: dict[str, str] = {
    "builder": "coalition_seeker",
    "coalition": "coalition_seeker",
    "bonded": "loyal_partner",
    "grudge": "grudger",
    "balancer": "leader_pressure",
    "climber": "opportunist",
    "closer": "endgame_sniper",
    "mediator": "diplomat",
    "echo": "crowd_follower",
}

# Internal strategy ids. Sourced from the rule registry so the validator and the
# planner can never disagree about which strategies exist — registering a new
# rule function in plan_rules.py is all it takes to make a strategy valid. A few
# display names differ (set in bot_presets.py): grudger -> "Long Memory",
# leader_pressure -> "Giant Slayer", endgame_sniper -> "The Closer",
# diplomat -> "Instigator". Ids are kept stable so saved bots and tests don't
# churn.
VALID_STRATEGIES: frozenset[str] = plan_rules.registered_strategy_ids()

ACTION_INTENTS = {
    "start_partnership",
    "keep_partner",
    "test_offer",
    "reward_helper",
    "repair_trust",
    "protect_victim",
    "punish_attacker",
    "hurt_leader",
    "block_rival",
    "betray_helper",
    "hoard_protect_score",
    "wait_and_watch",
    "climb_safely",
    "follow_crowd",
}


def normalize_strategy_name(name: str) -> str:
    return STRATEGY_ALIASES.get(name.strip().lower().replace(" ", "_"), name.strip().lower())


def choose_action_plan(
    context: BotContext,
    profile: BotProfile,
    trust_map: dict[str, int],
    signals: Sequence[TalkSignal],
) -> list[BotPlan | None]:
    """Ranked candidate plans for this bot's strategy, best first.

    Reads the table once into a :class:`~app.engine.bots.plan_rules.PlanInputs`
    bundle, then dispatches to the strategy's registered rule function. The
    seeded selectors stay in this module (their original home) and are injected
    into the bundle, so the rule functions in ``plan_rules`` carry no determinism
    logic of their own. The runtime picks the first applicable, valid row.
    """
    strategy = normalize_strategy_name(profile.strategy)
    inputs = plan_rules.build_plan_inputs(
        context,
        profile,
        trust_map,
        signals,
        best_partner=_best_partner,
        most_hostile=_most_hostile,
        probe_target=_probe_target,
        recent_helper=_recent_helper,
        recent_attacker=_recent_attacker,
        recent_aggressor=_recent_aggressor,
        cooperation_offers=_cooperation_offers,
        leader=_leader,
        leader_gap=_leader_gap_from_you,
        should_probe=_should_probe,
        crowd_plan=_copy_crowd_action,
    )
    return plan_rules.plan_for_strategy(strategy, inputs)


def _should_probe(context: BotContext, profile: BotProfile) -> bool:
    """Every once in a while — roughly one turn in three — try a test HELP."""
    return _seed_int(profile, context, "probe") % 3 == 0


def _probe_target(
    context: BotContext, profile: BotProfile, trust_map: dict[str, int]
) -> str | None:
    """Pick someone to feel out: a non-hostile other, favoring higher trust and
    rotating across turns so it tries different players until one gives back."""
    others = [aid for aid in context.all_agent_ids if aid != context.your_agent_id]
    candidates = [aid for aid in others if trust_map.get(aid, 0) >= 0] or others
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda aid: (-trust_map.get(aid, 0), _seed_int(profile, context, aid, context.turn)),
    )


def _best_partner(
    context: BotContext, profile: BotProfile, trust_map: dict[str, int], *, minimum: int
) -> str | None:
    candidates = [aid for aid, score in trust_map.items() if score >= minimum]
    if not candidates:
        return None
    return min(candidates, key=lambda aid: (-trust_map[aid], _seed_int(profile, context, aid)))


def _most_hostile(
    context: BotContext, profile: BotProfile, trust_map: dict[str, int]
) -> str | None:
    candidates = [aid for aid, score in trust_map.items() if score <= -20]
    if not candidates:
        return None
    return min(candidates, key=lambda aid: (trust_map[aid], _seed_int(profile, context, aid)))


def _cooperation_offers(signals: Sequence[TalkSignal], me: str) -> list[str]:
    return [s.speaker_id for s in signals if s.kind == "cooperation_offer" and s.target_id == me]


def _recent_helper(context: BotContext, trust_map: dict[str, int]) -> str | None:
    records = records_for_latest_round(context.history)
    helpers = [r.actor_id for r in records if r.target_id == context.your_agent_id and r.action == "HELP"]
    return _choose_from_candidates(context, trust_map, helpers, favor_high=True)


def _recent_attacker(context: BotContext, trust_map: dict[str, int]) -> str | None:
    records = records_for_latest_round(context.history)
    attackers = [r.actor_id for r in records if r.target_id == context.your_agent_id and r.action == "HURT"]
    return _choose_from_candidates(context, trust_map, attackers, favor_high=False)


def _recent_aggressor(context: BotContext, trust_map: dict[str, int]) -> str | None:
    """Someone who HURT anyone last turn (not me as the actor)."""
    records = records_for_latest_round(context.history)
    aggressors = [
        r.actor_id
        for r in records
        if r.action == "HURT" and r.actor_id != context.your_agent_id
    ]
    return _choose_from_candidates(context, trust_map, aggressors, favor_high=True)


def crowd_choice(context: BotContext) -> tuple[str, str | None] | None:
    """Pick the move that copies the crowd's last-turn majority action.

    This is the single source of truth for the crowd-follower algorithm; both
    the strategy planner (:func:`_copy_crowd_action`) and the runtime move
    builder call it so the two can never drift.

    Returns ``None`` when there is no crowd signal at all (empty history or no
    non-defaulted records). Otherwise returns ``(action, target_id)``:

    * the most-common action in the latest non-defaulted turn, broken by the
      HELP < HURT < HOARD priority order;
    * for a HELP/HURT action, the most-targeted player, broken by the seeded
      tiebreak ``_seed_int(context, aid)``.

    A HELP/HURT action with no eligible target collapses to ``("HOARD", None)``
    so callers never have to handle a targetless attack/help.
    """
    records = records_for_latest_round(context.history)
    if not records:
        return None
    counts = Counter(r.action for r in records)
    best_count = max(counts.values())
    action_order = ["HELP", "HURT", "HOARD"]
    best_actions = [a for a, count in counts.items() if count == best_count]
    best_action = min(best_actions, key=lambda a: action_order.index(a))
    if best_action == "HOARD":
        return ("HOARD", None)
    targets = [r.target_id for r in records if r.action == best_action and r.target_id is not None]
    if not targets:
        return ("HOARD", None)
    target_counts = Counter(targets)
    top = max(target_counts.values())
    candidates = [t for t, c in target_counts.items() if c == top]
    target = min(candidates, key=lambda aid: _seed_int(context, aid))
    return (best_action, target)


def _copy_crowd_action(context: BotContext) -> BotPlan | None:
    choice = crowd_choice(context)
    if choice is None:
        return None
    action, target = choice
    if action == "HOARD":
        return BotPlan("follow_crowd", None, "crowd hoarded")
    return BotPlan("follow_crowd", target, f"copy {action.lower()}")


def _choose_from_candidates(
    context: BotContext,
    trust_map: dict[str, int],
    candidates: Sequence[str],
    *,
    favor_high: bool,
) -> str | None:
    if not candidates:
        return None
    unique = list(dict.fromkeys(candidates))
    if favor_high:
        # Cooperative pick (reward a helper, repay an aggressor): never extend it
        # to a known traitor.
        trusted = [aid for aid in unique if trust_map.get(aid, 0) > HOSTILE_TRUST]
        if not trusted:
            return None
        return min(trusted, key=lambda aid: (-trust_map.get(aid, 0), _seed_int(context, aid)))
    return min(unique, key=lambda aid: (trust_map.get(aid, 0), _seed_int(context, aid)))


def latest_turn(history: Sequence[ActionRecord]) -> tuple[int, int] | None:
    """The most recent ``(round, turn)`` that has a non-defaulted record.

    Shared primitive: the crowd-follower, the recent-actor lookups, and the
    trust model all key off "the latest turn that actually happened", so they
    use this one definition rather than each re-deriving the max.
    """
    turns = [(r.round, r.turn) for r in history if not r.was_defaulted]
    return max(turns) if turns else None


def records_for_latest_round(history: Sequence[ActionRecord]) -> list[ActionRecord]:
    latest = latest_turn(history)
    if latest is None:
        return []
    return [r for r in history if (r.round, r.turn) == latest and not r.was_defaulted]


def _leader(context: BotContext) -> str | None:
    if not context.scoreboard:
        return None
    top = max(row.round_score for row in context.scoreboard)
    tied = [row.agent_id for row in context.scoreboard if row.round_score == top]
    return min(tied, key=lambda aid: _seed_int(context, aid))


def _leader_gap(context: BotContext, leader: str | None) -> int:
    if leader is None:
        return 0
    leader_score = next((row.round_score for row in context.scoreboard if row.agent_id == leader), 0)
    me_score = next(
        (row.round_score for row in context.scoreboard if row.agent_id == context.your_agent_id),
        0,
    )
    return leader_score - me_score


def _leader_gap_from_you(context: BotContext, leader: str | None) -> int:
    return _leader_gap(context, leader)


def _seed_int(*parts: object) -> int:
    payload = "||".join(
        p.seed_basis() if isinstance(p, BotContext) else str(p) for p in parts
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return int(digest[:16], 16)
