"""Strategy selection for Sims."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Sequence

from app.engine.game_records import ActionRecord
from app.schemas.agent import ScoreboardRow

from .phrases import PHRASES
from .signals import TalkSignal
from .types import SimContext, SimPlan, SimProfile

_PHRASE_INTENTS: tuple[str, ...] = tuple(PHRASES.keys())

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

VALID_STRATEGIES = {
    "coalition_seeker",
    "loyal_partner",
    "grudger",
    "leader_pressure",
    "opportunist",
    "endgame_sniper",
    "diplomat",
    "crowd_follower",
    "coin_flip",
}

TALK_INTENTS = {
    "coalition_seeker",
    "loyal_partner",
    "grudger",
    "leader_pressure",
    "opportunist",
    "endgame_sniper",
    "diplomat",
    "crowd_follower",
    "coin_flip",
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
    "endgame_hurt",
    "block_rival",
    "hoard_protect_score",
    "wait_and_watch",
    "climb_safely",
    "follow_crowd",
}


def normalize_strategy_name(name: str) -> str:
    return STRATEGY_ALIASES.get(name.strip().lower().replace(" ", "_"), name.strip().lower())


def choose_talk_plan(
    context: SimContext,
    profile: SimProfile,
    trust_map: dict[str, int],
    signals: Sequence[TalkSignal],
) -> SimPlan:
    strategy = normalize_strategy_name(profile.strategy)
    partner = _best_partner(context, profile, trust_map, minimum=20)
    hostile = _most_hostile(context, profile, trust_map)
    leader = _leader(context)
    offers = _cooperation_offers(signals, context.your_agent_id)
    attackers = _attackers(signals, context.your_agent_id)

    if strategy == "coalition_seeker":
        if partner is not None:
            return SimPlan("propose_partnership", partner, f"trust={trust_map[partner]}")
        if offers:
            target = _best_signal_target(context, profile, trust_map, offers)
            return SimPlan("propose_partnership", target, "offered partnership")
        return SimPlan("observe_table", None, "no strong partner yet")

    if strategy == "loyal_partner":
        if partner is not None:
            return SimPlan("confirm_partner", partner, f"partner={partner}")
        if hostile is not None:
            return SimPlan("claim_repair", hostile, f"hostile={hostile}")
        return SimPlan("observe_table", None, "waiting")

    if strategy == "grudger":
        if hostile is not None:
            return SimPlan("warn_attacker", hostile, f"hostile={hostile}")
        if attackers:
            return SimPlan("ask_truce", attackers[0], "recent attack")
        return SimPlan("observe_table", None, "clean table")

    if strategy == "leader_pressure":
        if leader is not None and _leader_gap(context, leader) >= 8:
            return SimPlan("warn_leader", leader, "leader is far ahead")
        return SimPlan("claim_score_focus", None, "watching the board")

    if strategy == "opportunist":
        if _leader_gap_from_you(context, leader) <= -5:
            return SimPlan("claim_score_focus", None, "ahead")
        if offers:
            target = _best_signal_target(context, profile, trust_map, offers)
            return SimPlan("propose_partnership", target, "offer available")
        return SimPlan("claim_score_focus", None, "score first")

    if strategy == "endgame_sniper":
        if 8 <= context.turn <= 10 and leader is not None and _leader_gap(context, leader) >= 8:
            return SimPlan("warn_leader", leader, "late pressure window")
        if context.turn <= 7 and partner is not None:
            return SimPlan("propose_partnership", partner, "early partnership")
        return SimPlan("observe_table", None, "waiting for the finish")

    if strategy == "diplomat":
        if hostile is not None:
            return SimPlan("claim_repair", hostile, "repairing")
        if partner is not None:
            return SimPlan("ask_truce", partner, "friendly lane")
        return SimPlan("observe_table", None, "staying calm")

    if strategy == "crowd_follower":
        if leader is not None and _leader_gap(context, leader) >= 8:
            return SimPlan("warn_leader", leader, "table pressure")
        return SimPlan("observe_table", None, "watching momentum")

    if strategy == "coin_flip":
        others = [aid for aid in context.all_agent_ids if aid != context.your_agent_id]
        intent = _PHRASE_INTENTS[_seed_int(profile, context, "coin_flip_talk_intent") % len(_PHRASE_INTENTS)]
        talk_target: str | None = (
            others[_seed_int(profile, context, "coin_flip_talk_target") % len(others)]
            if others
            else None
        )
        return SimPlan(intent, talk_target, "coin flip")

    return SimPlan("observe_table", None, "fallback")


def choose_action_plan(
    context: SimContext,
    profile: SimProfile,
    trust_map: dict[str, int],
    signals: Sequence[TalkSignal],
) -> list[SimPlan | None]:
    strategy = normalize_strategy_name(profile.strategy)
    partner = _best_partner(context, profile, trust_map, minimum=20)
    strong_partner = _best_partner(context, profile, trust_map, minimum=60)
    helper = _recent_helper(context, trust_map)
    attacker = _recent_attacker(context, trust_map)
    leader = _leader(context)
    leader_gap = _leader_gap_from_you(context, leader)
    offers = _cooperation_offers(signals, context.your_agent_id)
    victim = _recent_victim(context, trust_map)
    rival = _closest_rival(context)

    if strategy == "coalition_seeker":
        return [
            SimPlan("keep_partner", strong_partner, "trusted partner") if strong_partner else None,
            SimPlan("test_offer", offers[0], "cooperation offer") if offers else None,
            SimPlan("reward_helper", helper, "recent helper") if helper else None,
            SimPlan("start_partnership", partner, "best partner") if partner else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "loyal_partner":
        return [
            SimPlan("keep_partner", strong_partner, "strong partner") if strong_partner else None,
            SimPlan("repair_trust", partner, "repair lane") if partner else None,
            SimPlan("punish_attacker", attacker, "recent attack") if attacker else None,
            SimPlan("start_partnership", partner, "fallback partner") if partner else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "grudger":
        return [
            SimPlan("punish_attacker", attacker, "attacker") if attacker else None,
            SimPlan("punish_attacker", _most_hostile(context, profile, trust_map), "hostile")
            if _most_hostile(context, profile, trust_map)
            else None,
            SimPlan("hurt_leader", leader, "leader pressure") if leader is not None and leader_gap >= 12 else None,
            SimPlan("reward_helper", helper, "helper") if helper else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "leader_pressure":
        return [
            SimPlan("hurt_leader", leader, "runaway leader") if leader is not None and leader_gap >= 12 else None,
            SimPlan("block_rival", rival, "close rival") if rival is not None and _leader_gap(context, leader) < 12 else None,
            SimPlan("reward_helper", helper, "helper") if helper else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "opportunist":
        return [
            SimPlan("hoard_protect_score", None, "ahead") if leader_gap <= -5 else None,
            SimPlan("test_offer", offers[0], "offer") if offers and 0 < leader_gap <= 7 else None,
            SimPlan("block_rival", rival, "falling behind") if rival is not None and leader_gap > 7 else None,
            SimPlan("hurt_leader", leader, "catchup") if leader is not None and leader_gap > 10 else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "endgame_sniper":
        if 8 <= context.turn <= 10:
            return [
                SimPlan("hurt_leader", leader, "endgame") if leader is not None and leader_gap >= 8 else None,
                SimPlan("keep_partner", partner, "late partner") if partner else None,
                SimPlan("reward_helper", helper, "late helper") if helper else None,
                SimPlan("hoard_protect_score", None, "fallback"),
            ]
        return [
            SimPlan("keep_partner", partner, "early partner") if partner else None,
            SimPlan("reward_helper", helper, "helper") if helper else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "diplomat":
        return [
            SimPlan("repair_trust", victim, "protect victim") if victim else None,
            SimPlan("protect_victim", victim, "victim") if victim else None,
            SimPlan("reward_helper", helper, "helper") if helper else None,
            SimPlan("punish_attacker", attacker, "last resort") if attacker else None,
            SimPlan("hoard_protect_score", None, "fallback"),
        ]

    if strategy == "crowd_follower":
        copied = _copy_crowd_action(context)
        return [copied] if copied is not None else [SimPlan("hoard_protect_score", None, "no crowd signal")]

    if strategy == "coin_flip":
        others = [aid for aid in context.all_agent_ids if aid != context.your_agent_id]
        scores = {row.agent_id: row.round_score for row in context.scoreboard}
        hurtable = [aid for aid in others if scores.get(aid, 0) > 0]
        options: list[str] = ["HOARD"]
        if others:
            options.append("HELP")
        if hurtable:
            options.append("HURT")
        action = options[_seed_int(profile, context, "coin_flip_action") % len(options)]
        if action == "HELP":
            target = others[_seed_int(profile, context, "coin_flip_target") % len(others)]
            return [SimPlan("test_offer", target, "coin flip")]
        if action == "HURT":
            target = hurtable[_seed_int(profile, context, "coin_flip_target") % len(hurtable)]
            return [SimPlan("punish_attacker", target, "coin flip")]
        return [SimPlan("hoard_protect_score", None, "coin flip")]

    return [SimPlan("hoard_protect_score", None, "fallback")]


def _best_partner(
    context: SimContext, profile: SimProfile, trust_map: dict[str, int], *, minimum: int
) -> str | None:
    candidates = [aid for aid, score in trust_map.items() if score >= minimum]
    if not candidates:
        return None
    return min(candidates, key=lambda aid: (-trust_map[aid], _seed_int(profile, context, aid)))


def _most_hostile(
    context: SimContext, profile: SimProfile, trust_map: dict[str, int]
) -> str | None:
    candidates = [aid for aid, score in trust_map.items() if score <= -20]
    if not candidates:
        return None
    return min(candidates, key=lambda aid: (trust_map[aid], _seed_int(profile, context, aid)))


def _best_signal_target(
    context: SimContext, profile: SimProfile, trust_map: dict[str, int], targets: Sequence[str]
) -> str:
    return min(targets, key=lambda aid: (-trust_map.get(aid, 0), _seed_int(profile, context, aid)))


def _cooperation_offers(signals: Sequence[TalkSignal], me: str) -> list[str]:
    return [s.speaker_id for s in signals if s.kind == "cooperation_offer" and s.target_id == me]


def _attackers(signals: Sequence[TalkSignal], me: str) -> list[str]:
    return [s.speaker_id for s in signals if s.kind == "threat" and s.target_id == me]


def _recent_helper(context: SimContext, trust_map: dict[str, int]) -> str | None:
    records = _records_for_latest_round(context.history)
    helpers = [r.actor_id for r in records if r.target_id == context.your_agent_id and r.action == "HELP"]
    return _choose_from_candidates(context, trust_map, helpers, favor_high=True)


def _recent_attacker(context: SimContext, trust_map: dict[str, int]) -> str | None:
    records = _records_for_latest_round(context.history)
    attackers = [r.actor_id for r in records if r.target_id == context.your_agent_id and r.action == "HURT"]
    return _choose_from_candidates(context, trust_map, attackers, favor_high=False)


def _recent_victim(context: SimContext, trust_map: dict[str, int]) -> str | None:
    records = _records_for_latest_round(context.history)
    victims = [
        r.target_id
        for r in records
        if r.target_id is not None
        and r.target_id != context.your_agent_id
        and r.action == "HURT"
        and trust_map.get(r.target_id, 0) > -20
    ]
    return _choose_from_candidates(context, trust_map, victims, favor_high=True)


def _closest_rival(context: SimContext) -> str | None:
    ordered = _ordered_scores(context.scoreboard)
    me = next((row for row in ordered if row.agent_id == context.your_agent_id), None)
    if me is None:
        return None
    rivals = [row for row in ordered if row.agent_id != context.your_agent_id]
    if not rivals:
        return None
    rivals.sort(key=lambda row: (-row.round_score, _seed_int(context, row.agent_id)))
    return rivals[0].agent_id


def _copy_crowd_action(context: SimContext) -> SimPlan | None:
    if not context.history:
        return None
    latest = max((r.round, r.turn) for r in context.history if not r.was_defaulted)
    turn_actions = [r for r in context.history if (r.round, r.turn) == latest and not r.was_defaulted]
    if not turn_actions:
        return None
    counts = Counter(r.action for r in turn_actions)
    best_count = max(counts.values())
    action_order = ["HELP", "HURT", "HOARD"]
    best_actions = [a for a, count in counts.items() if count == best_count]
    best_action = min(best_actions, key=lambda a: action_order.index(a))
    if best_action == "HOARD":
        return SimPlan("follow_crowd", None, "crowd hoarded")
    targets = [r.target_id for r in turn_actions if r.action == best_action and r.target_id is not None]
    target = None
    if targets:
        counts_t = Counter(targets)
        top = max(counts_t.values())
        candidates = [t for t, c in counts_t.items() if c == top]
        target = min(candidates, key=lambda aid: _seed_int(context, aid))
    kind = "follow_crowd"
    return SimPlan(kind, target, f"copy {best_action.lower()}")


def _choose_from_candidates(
    context: SimContext,
    trust_map: dict[str, int],
    candidates: Sequence[str],
    *,
    favor_high: bool,
) -> str | None:
    if not candidates:
        return None
    unique = list(dict.fromkeys(candidates))
    if favor_high:
        return min(unique, key=lambda aid: (-trust_map.get(aid, 0), _seed_int(context, aid)))
    return min(unique, key=lambda aid: (trust_map.get(aid, 0), _seed_int(context, aid)))


def _records_for_latest_round(history: Sequence[ActionRecord]) -> list[ActionRecord]:
    if not history:
        return []
    latest = max((r.round, r.turn) for r in history if not r.was_defaulted)
    return [r for r in history if (r.round, r.turn) == latest and not r.was_defaulted]


def _leader(context: SimContext) -> str | None:
    if not context.scoreboard:
        return None
    top = max(row.round_score for row in context.scoreboard)
    tied = [row.agent_id for row in context.scoreboard if row.round_score == top]
    return min(tied, key=lambda aid: _seed_int(context, aid))


def _leader_gap(context: SimContext, leader: str | None) -> int:
    if leader is None:
        return 0
    leader_score = next((row.round_score for row in context.scoreboard if row.agent_id == leader), 0)
    me_score = next(
        (row.round_score for row in context.scoreboard if row.agent_id == context.your_agent_id),
        0,
    )
    return leader_score - me_score


def _leader_gap_from_you(context: SimContext, leader: str | None) -> int:
    return _leader_gap(context, leader)


def _ordered_scores(rows: Sequence[ScoreboardRow]) -> list[ScoreboardRow]:
    return sorted(rows, key=lambda row: (-row.round_score, row.agent_id))


def _seed_int(*parts: object) -> int:
    payload = "||".join(
        p.seed_basis() if isinstance(p, SimContext) else str(p) for p in parts
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return int(digest[:16], 16)
