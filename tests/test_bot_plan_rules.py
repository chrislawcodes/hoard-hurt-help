"""Locks the strategy-registry structure that ends the bots' recurring copy-paste.

The per-strategy plan rules now live in one registry (``plan_rules``) built from
shared row helpers, and ``strategies.choose_action_plan`` is a thin dispatcher.
These tests pin that contract so the structure can't quietly rot back into a
hand-written ``if strategy == ...`` chain:

* every valid strategy is reachable through the registry (no orphan ids),
* the registry is the single source for ``VALID_STRATEGIES``,
* registering a duplicate id is a hard error (a copy-paste that forgets to
  rename is caught at import, not in production),
* the shared row builders behave as the rules rely on,
* an unknown strategy degrades to the lone hoard fallback, same as before.
"""

from __future__ import annotations

import pytest

from app.engine.bots import plan_rules
from app.engine.bots.strategies import VALID_STRATEGIES, choose_action_plan
from app.engine.bots.types import BotPlan, BotProfile
from tests.test_bots_engine import _context


def test_registry_is_the_single_source_for_valid_strategies() -> None:
    # The validator (VALID_STRATEGIES) and the planner read the same registry, so
    # they can never disagree about which strategies exist.
    assert VALID_STRATEGIES == plan_rules.registered_strategy_ids()
    assert "coalition_seeker" in VALID_STRATEGIES
    assert "coin_flip" not in VALID_STRATEGIES


def test_every_valid_strategy_has_a_rule_and_returns_a_ranking() -> None:
    # Each strategy resolves to a non-empty ranked list whose final, always-
    # applicable row is the hoard fallback (the invariant every bot shares).
    ctx = _context()
    for strategy in sorted(VALID_STRATEGIES):
        profile = BotProfile(
            strategy=strategy, truthfulness=80, trust_model="even", seed=3, version="v1"
        )
        rows = choose_action_plan(ctx, profile, {}, [])
        assert rows, strategy
        last = rows[-1]
        assert isinstance(last, BotPlan) and last is not None, strategy
        assert last.intent == "hoard_protect_score", strategy


def test_unknown_strategy_falls_back_to_hoard() -> None:
    # An unrecognized id degrades to a single hoard row, matching the old planner.
    ctx = _context()
    profile = BotProfile(
        strategy="not_a_real_strategy", truthfulness=80, trust_model="even", seed=3, version="v1"
    )
    rows = choose_action_plan(ctx, profile, {}, [])
    assert rows == [BotPlan("hoard_protect_score", None, "fallback")]


def test_register_strategy_rejects_a_duplicate_id() -> None:
    # The decorator is what makes copy-paste fail loud: re-registering an existing
    # id raises instead of silently shadowing a strategy.
    with pytest.raises(ValueError, match="already registered"):

        @plan_rules.register_strategy("coalition_seeker")
        def _dupe(_inputs: plan_rules.PlanInputs) -> list[BotPlan | None]:
            return [plan_rules.hoard("dupe")]


def test_row_builders_drop_rows_with_no_target_and_keep_the_fallback() -> None:
    # The shared builders encode the "BotPlan(...) if cond else None" idiom the
    # strategies used to re-type; verify both arms.
    assert plan_rules.help_if("reward_helper", None, "no one") is None
    assert plan_rules.help_if("reward_helper", "AI_2", "helper") == BotPlan(
        "reward_helper", "AI_2", "helper"
    )
    assert plan_rules.hurt_if("hurt_leader", "AI_3", "leader", when=False) is None
    assert plan_rules.hurt_if("hurt_leader", "AI_3", "leader", when=True) == BotPlan(
        "hurt_leader", "AI_3", "leader"
    )
    assert plan_rules.betray_if(None, "buzzer") is None
    assert plan_rules.hoard("fallback") == BotPlan("hoard_protect_score", None, "fallback")
