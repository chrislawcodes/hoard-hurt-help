"""D5: determinism regression pins for the seeded trust-tiebreak selectors.

These lock the current behavior of the bot selectors so the dedup decision (keep
them as distinct one-line idioms — see the D5 ledger) is guarded: the seeded
tiebreak stays deterministic, trust ordering is respected, and `_probe_target`'s
seed still depends on `context.turn`. Reuses the `_context` builder from
test_bots_engine.
"""
from __future__ import annotations

from app.engine.bots.strategies import (
    _best_partner,
    _most_hostile,
    _probe_target,
)
from app.engine.bots.types import BotProfile
from tests.test_bots_engine import _context


def _profile() -> BotProfile:
    return BotProfile(
        strategy="cautious", truthfulness=80, trust_model="balanced", seed=7, version="v1"
    )


def test_selectors_are_deterministic() -> None:
    ctx, prof = _context(), _profile()
    tmap = {"AI_2": 10, "AI_3": 10, "AI_10": 3}
    assert _best_partner(ctx, prof, tmap, minimum=5) == _best_partner(ctx, prof, tmap, minimum=5)
    hostile = {"AI_2": -30, "AI_3": -25}
    assert _most_hostile(ctx, prof, hostile) == _most_hostile(ctx, prof, hostile)
    assert _probe_target(ctx, prof, {}) == _probe_target(ctx, prof, {})


def test_best_partner_prefers_highest_trust_above_minimum() -> None:
    ctx, prof = _context(), _profile()
    # AI_2 clearly highest; AI_10 below the minimum and excluded.
    assert _best_partner(ctx, prof, {"AI_2": 30, "AI_3": 10, "AI_10": 2}, minimum=5) == "AI_2"
    # No one meets the minimum → None.
    assert _best_partner(ctx, prof, {"AI_2": 1}, minimum=5) is None


def test_most_hostile_prefers_lowest_trust() -> None:
    ctx, prof = _context(), _profile()
    assert _most_hostile(ctx, prof, {"AI_2": -30, "AI_3": -25}) == "AI_2"
    # Nobody at or below -20 → None.
    assert _most_hostile(ctx, prof, {"AI_2": -5}) is None


def test_probe_target_seed_depends_on_turn() -> None:
    """A tie in trust (all 0) is broken by the seed, which carries context.turn —
    so different turns produce different picks. Guards against dropping the turn
    term from `_probe_target`'s seed."""
    prof = _profile()
    picks = {
        _probe_target(_context(turn=t), prof, {})  # empty trust → all-tied → seed decides
        for t in range(25)
    }
    assert len([p for p in picks if p is not None]) >= 1
    assert len(picks) >= 2  # turn actually changes the pick
