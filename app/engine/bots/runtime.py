"""High-level orchestration for deterministic bots."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from app.engine.bots.signals import TalkSignal
from app.models.agent import Agent, AgentKind
from app.schemas.agent import ScoreboardRow

from .phrases import render_phrase
from .signals import extract_talk_signals
from .strategies import (
    VALID_STRATEGIES,
    _seed_int,
    choose_action_plan,
    normalize_strategy_name,
)
from .trust import compute_trust_map
from .types import BotActionDecision, BotContext, BotPlan, BotProfile, BotTalkDecision


def validate_bot_profile_fields(
    *,
    kind: AgentKind | None,
    bot_strategy: str | None,
    bot_truthfulness: int | None,
    bot_trust_model: str | None,
    bot_seed: int | None,
    bot_version: str | None,
) -> None:
    """Validate that bot profile fields are present and internally consistent.

    Raises :class:`ValueError` with a descriptive message on the first problem
    found. Call this at creation/edit time so malformed bots are rejected before
    they ever reach a game seat.
    """
    if kind != AgentKind.BOT:
        raise ValueError("agent is not a bot")
    missing = [
        field
        for field, value in [
            ("bot_strategy", bot_strategy),
            ("bot_truthfulness", bot_truthfulness),
            ("bot_trust_model", bot_trust_model),
            ("bot_seed", bot_seed),
            ("bot_version", bot_version),
        ]
        if value is None
    ]
    if missing:
        raise ValueError(
            f"bot agent is missing required fields: {', '.join(missing)}"
        )
    # bot_strategy is not None here (checked above), so the assert is for mypy.
    assert bot_strategy is not None
    normalized = normalize_strategy_name(bot_strategy)
    if normalized not in VALID_STRATEGIES:
        raise ValueError(
            f"unknown bot strategy {bot_strategy!r}; "
            f"valid strategies are: {sorted(VALID_STRATEGIES)}"
        )


def build_bot_profile(agent: Agent) -> BotProfile:
    validate_bot_profile_fields(
        kind=agent.kind,
        bot_strategy=agent.bot_strategy,
        bot_truthfulness=agent.bot_truthfulness,
        bot_trust_model=agent.bot_trust_model,
        bot_seed=agent.bot_seed,
        bot_version=agent.bot_version,
    )
    # All fields are guaranteed non-None by validate_bot_profile_fields.
    assert agent.bot_strategy is not None
    assert agent.bot_truthfulness is not None
    assert agent.bot_trust_model is not None
    assert agent.bot_seed is not None
    assert agent.bot_version is not None
    return BotProfile(
        strategy=normalize_strategy_name(agent.bot_strategy),
        truthfulness=agent.bot_truthfulness,
        trust_model=agent.bot_trust_model,
        seed=agent.bot_seed,
        version=agent.bot_version,
        fixture_pack=agent.bot_fixture_pack,
    )


# Map the action the bot has decided on to the talk that telegraphs it. Both
# tables key off the action-plan intent so the spoken line keeps the flavor of
# *why* the bot helps or hits (a repaid helper sounds different from a new ally).
_HELP_TALK_INTENTS: dict[str, str] = {
    "start_partnership": "offer_help",
    "test_offer": "offer_help",
    "keep_partner": "keep_ally",
    "reward_helper": "repay_help",
    "repair_trust": "mend_fences",
    "protect_victim": "mend_fences",
}
_HURT_TALK_INTENTS: dict[str, str] = {
    "punish_attacker": "hit_back",
    "hurt_leader": "curb_leader",
    "block_rival": "block_rival",
}


def choose_bot_talk_decision(context: BotContext, profile: BotProfile) -> BotTalkDecision:
    # Trust computed without this turn's talk: that is all the bot knows when it
    # speaks. Talk and act run as separate phases, so we decide the move here
    # using the same logic the act phase will, then talk *about* that move.
    trust_map = compute_trust_map(
        your_agent_id=context.your_agent_id,
        all_agent_ids=context.all_agent_ids,
        history=context.history,
        signals=[],
        trust_model=profile.trust_model,
    )
    # The act-phase seed keys off `context.phase`, so plan against an "act"
    # snapshot to keep the spoken intent aligned with the move that lands.
    action_context = replace(context, phase="act")
    plan, move = _decide_action(action_context, profile, trust_map, [])
    action = str(move["action"])
    talk_intent = _talk_intent_for(plan.intent, action)
    target = _talk_target(move, action_context, profile, trust_map)
    truth_mode = _choose_truth_mode(profile, context, plan.intent, "talk")
    message = render_phrase(
        talk_intent,
        truth_mode,
        seed=_seed_int(profile, context, talk_intent),
        target_name=target,
    )
    thinking = _thinking(profile, context, plan, truth_mode, trust_map)
    return BotTalkDecision(
        intent=talk_intent,
        truth_mode=truth_mode,
        message=message,
        thinking=thinking,
    )


def choose_bot_action_decision(context: BotContext, profile: BotProfile) -> BotActionDecision:
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
    plan, move = _decide_action(context, profile, trust_map, signals)
    return BotActionDecision(
        intent=plan.intent,
        move=move,
        thinking=_thinking(profile, context, plan, "n/a", trust_map),
    )


def _decide_action(
    context: BotContext,
    profile: BotProfile,
    trust_map: dict[str, int],
    signals: Sequence[TalkSignal],
) -> tuple[BotPlan, dict[str, str | None]]:
    """Pick the first legal move from the strategy's ranked plans."""
    for plan in choose_action_plan(context, profile, trust_map, signals):
        if plan is None:
            continue
        move = _plan_to_move(plan, context)
        if _move_is_valid(move, context):
            return plan, move
    return BotPlan("hoard_protect_score", None, "fallback"), {"action": "HOARD", "target_id": None}


def _talk_intent_for(action_intent: str, action: str) -> str:
    """Choose the talk intent that telegraphs the decided move."""
    if action == "HELP":
        return _HELP_TALK_INTENTS.get(action_intent, "offer_help")
    if action == "HURT":
        return _HURT_TALK_INTENTS.get(action_intent, "hit_back")
    return "play_own_game"


def _talk_target(
    move: dict[str, str | None],
    context: BotContext,
    profile: BotProfile,
    trust_map: dict[str, int],
) -> str | None:
    """Who the talk addresses: the move's target, or a stand-in for HOARD."""
    target = move.get("target_id")
    if target is not None:
        return target
    # HOARD has no target, but a `false` line still needs someone to (falsely)
    # promise help to. Address the most-trusted other player.
    others = [aid for aid in context.all_agent_ids if aid != context.your_agent_id]
    if not others:
        return None
    return min(others, key=lambda aid: (-trust_map.get(aid, 0), _seed_int(profile, context, aid)))


def _plan_to_move(plan: BotPlan, context: BotContext) -> dict[str, str | None]:
    if plan.intent in {"keep_partner", "start_partnership", "test_offer", "reward_helper", "repair_trust", "protect_victim"}:
        return {"action": "HELP", "target_id": plan.target_id}
    if plan.intent in {"punish_attacker", "hurt_leader", "block_rival"}:
        return {"action": "HURT", "target_id": plan.target_id}
    if plan.intent == "follow_crowd":
        # Copy the crowd's last majority action when possible.
        return _crowd_move(context)
    return {"action": "HOARD", "target_id": None}


def _crowd_move(context: BotContext) -> dict[str, str | None]:
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


def _move_is_valid(move: dict[str, str | None], context: BotContext) -> bool:
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


def _choose_truth_mode(profile: BotProfile, context: BotContext, intent: str, phase: str) -> str:
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
    profile: BotProfile,
    context: BotContext,
    plan: BotPlan,
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
