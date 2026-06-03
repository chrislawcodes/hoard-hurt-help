"""High-level orchestration for deterministic Sims."""

from __future__ import annotations

import hashlib
from typing import Sequence

from app.models.bot import Bot, BotKind
from app.schemas.agent import ScoreboardRow

from .phrases import render_phrase
from .signals import extract_talk_signals
from .strategies import choose_action_plan, choose_talk_plan, normalize_strategy_name
from .trust import compute_trust_map
from .types import SimActionDecision, SimContext, SimPlan, SimProfile, SimTalkDecision


def build_sim_profile(bot: Bot) -> SimProfile:
    if bot.kind != BotKind.SIM:
        raise ValueError("bot is not a Sim")
    if (
        bot.sim_strategy is None
        or bot.sim_truthfulness is None
        or bot.sim_trust_model is None
        or bot.sim_seed is None
        or bot.sim_version is None
    ):
        raise ValueError("sim bot is missing required Sim fields")
    return SimProfile(
        strategy=normalize_strategy_name(bot.sim_strategy),
        truthfulness=bot.sim_truthfulness,
        trust_model=bot.sim_trust_model,
        seed=bot.sim_seed,
        version=bot.sim_version,
        fixture_pack=bot.sim_fixture_pack,
    )


def choose_talk_decision(context: SimContext, profile: SimProfile) -> SimTalkDecision:
    trust_map = compute_trust_map(
        your_agent_id=context.your_agent_id,
        all_agent_ids=context.all_agent_ids,
        history=context.history,
        signals=[],
        trust_model=profile.trust_model,
    )
    plan = choose_talk_plan(context, profile, trust_map, [])
    truth_mode = _choose_truth_mode(profile, context, plan.intent, "talk")
    message = render_phrase(plan.intent, truth_mode, seed=_seed_int(profile, context, plan.intent))
    thinking = _thinking(profile, context, plan, truth_mode, trust_map)
    return SimTalkDecision(
        intent=plan.intent,
        truth_mode=truth_mode,
        message=message,
        thinking=thinking,
    )


def choose_action_decision(context: SimContext, profile: SimProfile) -> SimActionDecision:
    leader_id = _leader_id(context.scoreboard)
    signals = extract_talk_signals(
        context.current_talk_messages, all_agent_ids=context.all_agent_ids, leader_id=leader_id
    )
    trust_map = compute_trust_map(
        your_agent_id=context.your_agent_id,
        all_agent_ids=context.all_agent_ids,
        history=context.history,
        signals=signals,
        trust_model=profile.trust_model,
    )
    for plan in choose_action_plan(context, profile, trust_map, signals):
        if plan is None:
            continue
        move = _plan_to_move(plan, context)
        if _move_is_valid(move, context):
            return SimActionDecision(
                intent=plan.intent,
                move=move,
                thinking=_thinking(profile, context, plan, "n/a", trust_map),
            )
    fallback = SimPlan("hoard_protect_score", None, "fallback")
    return SimActionDecision(
        intent=fallback.intent,
        move={"action": "HOARD", "target_id": None},
        thinking=_thinking(profile, context, fallback, "n/a", trust_map),
    )


def _plan_to_move(plan: SimPlan, context: SimContext) -> dict[str, str | None]:
    if plan.intent in {"keep_partner", "start_partnership", "test_offer", "reward_helper", "repair_trust", "protect_victim"}:
        return {"action": "HELP", "target_id": plan.target_id}
    if plan.intent in {"punish_attacker", "hurt_leader", "endgame_hurt", "block_rival"}:
        return {"action": "HURT", "target_id": plan.target_id}
    if plan.intent == "follow_crowd":
        # Copy the crowd's last majority action when possible.
        return _crowd_move(context)
    return {"action": "HOARD", "target_id": None}


def _crowd_move(context: SimContext) -> dict[str, str | None]:
    if not context.history:
        return {"action": "HOARD", "target_id": None}
    latest = max((r.round, r.turn) for r in context.history if not r.was_defaulted)
    records = [r for r in context.history if (r.round, r.turn) == latest and not r.was_defaulted]
    if not records:
        return {"action": "HOARD", "target_id": None}
    counts: dict[str, int] = {}
    for record in records:
        counts[record.action] = counts.get(record.action, 0) + 1
    best_count = max(counts.values())
    action_order = {"HELP": 0, "HURT": 1, "HOARD": 2}
    best_actions = [action for action, count in counts.items() if count == best_count]
    action = min(best_actions, key=lambda a: action_order[a])
    if action == "HOARD":
        return {"action": "HOARD", "target_id": None}
    targets = [r.target_id for r in records if r.action == action and r.target_id is not None]
    if not targets:
        return {"action": "HOARD", "target_id": None}
    target_counts: dict[str, int] = {}
    for target in targets:
        target_counts[target] = target_counts.get(target, 0) + 1
    best_target_count = max(target_counts.values())
    best_targets = [t for t, count in target_counts.items() if count == best_target_count]
    target = min(best_targets, key=lambda aid: _seed_int(context, aid))
    return {"action": action, "target_id": target}


def _move_is_valid(move: dict[str, str | None], context: SimContext) -> bool:
    action = str(move.get("action", "")).upper()
    target = move.get("target_id")
    if action == "HOARD":
        return target is None
    if action not in {"HELP", "HURT"} or target is None:
        return False
    if target == context.your_agent_id:
        return False
    if target not in context.all_agent_ids:
        return False
    if action == "HURT":
        scores = {row.agent_id: row.round_score for row in context.scoreboard}
        return scores.get(target, 0) > 0
    return True


def _choose_truth_mode(profile: SimProfile, context: SimContext, intent: str, phase: str) -> str:
    value = profile.truthfulness
    if value >= 90:
        weights = [("honest", 80), ("partial", 20)]
    elif value >= 65:
        weights = [("honest", 55), ("partial", 35), ("quiet", 10)]
    elif value >= 45:
        weights = [("honest", 25), ("partial", 45), ("quiet", 20), ("misdirecting", 10)]
    elif value >= 25:
        weights = [("honest", 10), ("partial", 25), ("quiet", 25), ("misdirecting", 30), ("false", 10)]
    elif value >= 10:
        weights = [("honest", 5), ("partial", 15), ("quiet", 20), ("misdirecting", 35), ("false", 25)]
    else:
        weights = [("partial", 10), ("quiet", 20), ("misdirecting", 35), ("false", 35)]

    total = sum(weight for _, weight in weights)
    pick = _seed_int(profile, context, intent, phase) % total
    running = 0
    for mode, weight in weights:
        running += weight
        if pick < running:
            return mode
    return weights[-1][0]


def _thinking(
    profile: SimProfile,
    context: SimContext,
    plan: SimPlan,
    truth_mode: str,
    trust_map: dict[str, int],
) -> str:
    target = plan.target_id or "-"
    trust = trust_map.get(target, 0) if target != "-" else 0
    return (
        f"strategy={profile.strategy} intent={plan.intent} target={target} "
        f"truth={truth_mode} trust={trust} seed={profile.seed}"
    )


def _leader_id(scoreboard: Sequence[ScoreboardRow]) -> str | None:
    if not scoreboard:
        return None
    top = max(row.round_score for row in scoreboard)
    tied = [row.agent_id for row in scoreboard if row.round_score == top]
    return min(tied, key=lambda aid: _seed_int("leader", aid))


def _seed_int(*parts: object) -> int:
    payload = "||".join(str(p) for p in parts)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return int(digest[:16], 16)
