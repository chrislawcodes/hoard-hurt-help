"""Deterministic Sims engine."""

from .phrases import render_phrase
from .presets import (
    SIM_PACKS,
    SimPack,
    SimPackEntry,
    SimProfileChoice,
    expand_pack,
    pack_profile_choices,
    resolve_pack,
    resolve_profile_choice,
)
from .runtime import (
    build_bot_profile,
    build_sim_profile,
    choose_action_decision,
    choose_bot_action_decision,
    choose_bot_talk_decision,
    choose_talk_decision,
    validate_bot_profile_fields,
)
from .signals import TalkSignal, extract_talk_signals
from .strategies import choose_action_plan, choose_talk_plan, normalize_strategy_name
from .trust import compute_trust_map
from .types import (
    BotActionDecision,
    BotContext,
    BotPlan,
    BotProfile,
    BotTalkDecision,
    SimActionDecision,
    SimContext,
    SimPlan,
    SimProfile,
    SimTalkDecision,
)

__all__ = [
    "SIM_PACKS",
    "BotActionDecision",
    "BotContext",
    "BotPlan",
    "BotProfile",
    "BotTalkDecision",
    "SimPack",
    "SimPackEntry",
    "SimProfileChoice",
    "SimProfile",
    "SimContext",
    "SimPlan",
    "SimTalkDecision",
    "SimActionDecision",
    "build_bot_profile",
    "validate_bot_profile_fields",
    "TalkSignal",
    "choose_bot_action_decision",
    "choose_bot_talk_decision",
    "build_sim_profile",
    "choose_action_decision",
    "choose_action_plan",
    "choose_talk_decision",
    "choose_talk_plan",
    "compute_trust_map",
    "expand_pack",
    "extract_talk_signals",
    "pack_profile_choices",
    "normalize_strategy_name",
    "render_phrase",
    "resolve_pack",
    "resolve_profile_choice",
]
