"""Strategy selection for bots."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Sequence

from app.engine.game_records import ActionRecord

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

# Internal strategy ids. A few display names differ (set in bot_presets.py):
# grudger -> "Long Memory", leader_pressure -> "Giant Slayer",
# endgame_sniper -> "The Closer", diplomat -> "Instigator". Ids are kept stable
# so saved bots and tests don't churn.
VALID_STRATEGIES = {
    "coalition_seeker",
    "pragmatist",
    "loyal_partner",
    "grudger",
    "leader_pressure",
    "opportunist",
    "endgame_sniper",
    "diplomat",
    "crowd_follower",
}

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


# A player trusted at or below this reads as a known traitor / hostile. Bots
# won't extend fresh cooperation to them (they may still HURT them). A personal
# betrayal — or, under the less forgiving trust models, a witnessed one — clears it.
HOSTILE_TRUST = -20


def normalize_strategy_name(name: str) -> str:
    return STRATEGY_ALIASES.get(name.strip().lower().replace(" ", "_"), name.strip().lower())


def choose_action_plan(
    context: BotContext,
    profile: BotProfile,
    trust_map: dict[str, int],
    signals: Sequence[TalkSignal],
) -> list[BotPlan | None]:
    strategy = normalize_strategy_name(profile.strategy)
    partner = _best_partner(context, profile, trust_map, minimum=20)
    strong_partner = _best_partner(context, profile, trust_map, minimum=60)
    helper = _recent_helper(context, trust_map)
    attacker = _recent_attacker(context, trust_map)
    leader = _leader(context)
    leader_gap = _leader_gap_from_you(context, leader)
    offers = [
        speaker
        for speaker in _cooperation_offers(signals, context.your_agent_id)
        if trust_map.get(speaker, 0) > HOSTILE_TRUST
    ]

    if strategy == "coalition_seeker":
        return [
            BotPlan("keep_partner", strong_partner, "trusted partner") if strong_partner else None,
            BotPlan("test_offer", offers[0], "cooperation offer") if offers else None,
            BotPlan("reward_helper", helper, "recent helper") if helper else None,
            BotPlan("start_partnership", partner, "best partner") if partner else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "pragmatist":
        # Plays Coalition Seeker all round, then betrays at the buzzer: on the
        # final turn it HURTs the partner it expects to still HELP it. Because the
        # target is helping, the HURT lands for the full betrayal damage (-8) while
        # it still pockets their +4 — a swing big enough to steal the round-win. It
        # keeps talking cooperatively that turn (a false-mode bluff) so the partner
        # doesn't see it coming. Falls back to hoarding if it has no likely helper.
        if context.turn >= 7:
            betray_target = strong_partner or partner or helper
            return [
                BotPlan("betray_helper", betray_target, "betray at the buzzer")
                if betray_target
                else None,
                BotPlan("hoard_protect_score", None, "stop sharing"),
            ]
        return [
            BotPlan("keep_partner", strong_partner, "trusted partner") if strong_partner else None,
            BotPlan("test_offer", offers[0], "cooperation offer") if offers else None,
            BotPlan("reward_helper", helper, "recent helper") if helper else None,
            BotPlan("start_partnership", partner, "best partner") if partner else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "loyal_partner":
        # Commits to players who have actually helped it: keeps a proven partner,
        # reciprocates a fresh helper, defends the bond. With no partner yet, it
        # throws out the occasional test HELP to feel out who gives back — then
        # locks onto whoever reciprocates.
        probe = _probe_target(context, profile, trust_map) if _should_probe(context, profile) else None
        return [
            BotPlan("keep_partner", strong_partner, "proven partner") if strong_partner else None,
            BotPlan("repair_trust", partner, "trusted partner") if partner else None,
            BotPlan("reward_helper", helper, "reciprocate help") if helper else None,
            BotPlan("punish_attacker", attacker, "defend the bond") if attacker else None,
            BotPlan("test_offer", probe, "feel out a partner") if probe else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "grudger":
        # "Long Memory": remembers betrayal AND help. Punishes a fresh attacker,
        # then rewards a fresh helper, and still gangs up on a runaway leader.
        return [
            BotPlan("punish_attacker", attacker, "remembers betrayal") if attacker else None,
            BotPlan("reward_helper", helper, "remembers help") if helper else None,
            BotPlan("hurt_leader", leader, "runaway leader") if leader is not None and leader_gap >= 12 else None,
            BotPlan("punish_attacker", _most_hostile(context, profile, trust_map), "old grudge")
            if _most_hostile(context, profile, trust_map)
            else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "leader_pressure":
        # A contender that polices the leader: it builds its own score through a
        # partnership to climb, but drops everything to hit anyone who runs away
        # with the game (12+ ahead of it).
        return [
            BotPlan("hurt_leader", leader, "runaway leader") if leader is not None and leader_gap >= 12 else None,
            BotPlan("keep_partner", strong_partner, "proven partner") if strong_partner else None,
            BotPlan("reward_helper", helper, "reciprocate help") if helper else None,
            BotPlan("start_partnership", partner, "build to climb") if partner else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "opportunist":
        # Works the standings: cooperates when offered a deal OR when someone
        # actually helps it, and claws at the leader when it's falling behind.
        # (No more pointless rival-shoving; hoards when it's sitting pretty.)
        return [
            BotPlan("test_offer", offers[0], "took an offer") if offers else None,
            BotPlan("reward_helper", helper, "reward real help") if helper else None,
            BotPlan("hurt_leader", leader, "claw at the leader")
            if leader is not None and leader_gap >= 8
            else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "endgame_sniper":
        # Late game = the last couple of turns of the round (rounds are 7 turns).
        if context.turn >= 6:
            return [
                BotPlan("hurt_leader", leader, "endgame") if leader is not None and leader_gap >= 8 else None,
                BotPlan("keep_partner", partner, "late partner") if partner else None,
                BotPlan("reward_helper", helper, "late helper") if helper else None,
                BotPlan("hoard_protect_score", None, "fallback"),
            ]
        return [
            BotPlan("keep_partner", partner, "early partner") if partner else None,
            BotPlan("reward_helper", helper, "helper") if helper else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "diplomat":
        # Experiment: rewards aggression. Helps whoever attacked someone last
        # turn (a bounty for hurting), then repays its own helpers.
        aggressor = _recent_aggressor(context, trust_map)
        return [
            BotPlan("test_offer", aggressor, "reward the aggressor") if aggressor else None,
            BotPlan("reward_helper", helper, "repay a helper") if helper else None,
            BotPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "crowd_follower":
        # Sticks with anyone who helps it (so it can hold a partnership), but
        # otherwise just copies whatever the table did last turn.
        copied = _copy_crowd_action(context)
        return [
            BotPlan("reward_helper", helper, "stick with a helper") if helper else None,
            copied if copied is not None else BotPlan("hoard_protect_score", None, "no crowd signal"),
        ]

    return [BotPlan("hoard_protect_score", None, "fallback")]


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
