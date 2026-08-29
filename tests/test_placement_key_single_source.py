"""One home for the default finish-order rule.

Placement is per-game: the rating maths is shared, the finish order is the
game's. A game says how it ranks by implementing ``match_placement_key``, and
anything that ranks a match asks the game module for it.

Two things needed the *default* rule, and each carried its own copy:

- ``BaseGameModule.match_placement_key`` — what a game inherits unless it
  overrides;
- ``_resolve_placement_key`` in ``app/read_models/leaderboard.py`` — the
  fallback for a legacy ``game_type`` with no registered module.

They returned the same tuple, so nothing was wrong. The hazard was a change to
the default: add a tiebreak to one copy and legacy matches on the leaderboard
would go on being ranked by the old rule, silently and with nothing failing.
Both now call ``default_match_placement_key``.

The corpus is compared against that function as the oracle rather than against
hard-coded tuples, so a change to the default is inherited here instead of
needing this file updated in step — which is the drift all over again.

Liar's Dice deliberately ranks the other way round, and that divergence is
pinned below so nobody "fixes" it into agreement.
"""

from __future__ import annotations

import pytest

from app.games import get as get_game_module
from app.games.base import default_match_placement_key
from app.read_models.leaderboard import _resolve_placement_key

# Round wins are halved on a shared round, so floats are real inputs; scores go
# negative, and equal keys are a legitimate placement tie.
CASES = [
    (0.0, 0),
    (0.0, 7),
    (1.0, 0),
    (1.0, 7),
    (2.5, 13),
    (0.5, -4),
    (3.0, -100),
    (2.0, 2),
    (0.0, -1),
    (7.0, 999),
]


@pytest.mark.parametrize(("round_wins", "total_score"), CASES)
def test_legacy_fallback_matches_the_default(round_wins: float, total_score: int) -> None:
    """An unregistered game_type ranks exactly as the shared default does."""
    fallback = _resolve_placement_key("a-legacy-game-that-has-no-module")
    assert fallback(round_wins=round_wins, total_score=total_score) == (
        default_match_placement_key(round_wins=round_wins, total_score=total_score)
    )


@pytest.mark.parametrize(("round_wins", "total_score"), CASES)
def test_inheriting_game_matches_the_default(round_wins: float, total_score: int) -> None:
    """Hoard Hurt Help does not override, so it must rank as the default."""
    key = get_game_module("hoard-hurt-help").match_placement_key
    assert key(round_wins=round_wins, total_score=total_score) == (
        default_match_placement_key(round_wins=round_wins, total_score=total_score)
    )


@pytest.mark.parametrize(("round_wins", "total_score"), CASES)
def test_resolver_hands_back_the_games_own_rule(round_wins: float, total_score: int) -> None:
    """A registered game_type resolves to that game's key, not the default."""
    resolved = _resolve_placement_key("liars-dice")
    own = get_game_module("liars-dice").match_placement_key
    assert resolved(round_wins=round_wins, total_score=total_score) == (
        own(round_wins=round_wins, total_score=total_score)
    )


def test_liars_dice_divergence_is_deliberate_and_stays() -> None:
    """Liar's Dice ranks by score first — the opposite of the default.

    Pinned so a later "these two look the same, unify them" reading cannot
    quietly make it rank like PD. If this is ever changed on purpose, change it
    here too and say why.
    """
    key = get_game_module("liars-dice").match_placement_key
    assert key(round_wins=1.0, total_score=9) == (9.0, 1.0)
    assert default_match_placement_key(round_wins=1.0, total_score=9) == (1.0, 9.0)
    differing = [
        (rw, ts)
        for rw, ts in CASES
        if key(round_wins=rw, total_score=ts)
        != default_match_placement_key(round_wins=rw, total_score=ts)
    ]
    assert differing, "Liar's Dice should not rank identically to the default"


def test_default_orders_round_wins_above_score() -> None:
    """The rule itself, stated once: more round wins beats a higher score."""
    one_win_low_score = default_match_placement_key(round_wins=1.0, total_score=0)
    no_wins_high_score = default_match_placement_key(round_wins=0.0, total_score=500)
    assert one_win_low_score > no_wins_high_score
