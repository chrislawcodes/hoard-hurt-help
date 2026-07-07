"""Unit tests for `apply_inround_turn` — the viewer's running-score mirror.

Pure function (dict in, dict out). It approximates `resolve_turn` for lead
tracking, including betraying a helper: when a player HURTs someone who HELPs
them this same turn, the victim takes the normal HURT_POINTS and the attacker
gains a BETRAYAL_BONUS on top of the +HELP_POINTS they receive (attacker +8 /
victim -4), mirroring `resolve_turn`.
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


def test_mirror_betraying_a_helper_pays_the_attacker_eight():
    """Betraying a same-turn helper: attacker +8, victim -4 (mirrors resolve_turn).

    A HURTs B while B HELPs A. A gets +4 (B's help) + +4 (BETRAYAL_BONUS) = +8;
    B (from 10) takes the normal -4 → 6. The explicit dict pins the victim at
    start-4 so a stale victim -8 cannot pass.
    """
    out = apply_inround_turn(
        {"A": 0, "B": 10},
        [
            {"action": "HURT", "agent_id": "A", "target_id": "B"},
            {"action": "HELP", "agent_id": "B", "target_id": "A"},
        ],
    )
    assert out == {"A": 8, "B": 6}  # A: +4 help + +4 bonus; B: 10 - 4


def test_mirror_betrayed_victim_floors_per_hurt():
    """The mirror floors the betrayal victim per-hurt (its deliberate divergence).

    B HELPs A (A +8 via betrayal). A HURTs B. B starts at 5 → 5 - 4 = 1 (the
    changed damage of 4, not the old 8, moves this boundary: old would floor to 0).
    """
    out = apply_inround_turn(
        {"A": 0, "B": 5},
        [
            {"action": "HURT", "agent_id": "A", "target_id": "B"},
            {"action": "HELP", "agent_id": "B", "target_id": "A"},
        ],
    )
    assert out == {"A": 8, "B": 1}  # A: +8; B: 5 - 4 = 1 (would be 0 under the old -8)


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


def _betrayal_actions() -> list[dict]:
    """Action dicts shaped as `build_pd_replay_view` emits a same-turn betrayal.

    A HURTs B while B HELPs A: A's action carries betrayed_helper + betrayal_bonus;
    A's HURT display_delta is the victim's -4 (the +4 rides betrayal_bonus).
    """
    return [
        {"agent_id": "A", "action": "HURT", "target_id": "B", "mutual": False,
         "betrayal": False, "betrayed_helper": True, "betrayal_bonus": 4,
         "display_delta": -4, "was_defaulted": False, "message": ""},
        {"agent_id": "B", "action": "HELP", "target_id": "A", "mutual": False,
         "betrayal": False, "betrayed_helper": False, "betrayal_bonus": 0,
         "display_delta": 4, "was_defaulted": False, "message": ""},
    ]


def test_rc_data_threads_betrayed_helper_and_bonus():
    """The robot-circle JSON must carry `betrayed_helper`/`betrayal_bonus` so the
    animation can show the attacker's +4 (guard for the review-F2 silent-animation
    gap: without this thread the feed chip shows +4 but the animation nothing)."""
    scoreboard = [{"agent_id": "A"}, {"agent_id": "B"}]
    history = [
        {"round": 1, "turn": 1, "messages": [], "actions": _betrayal_actions()}
    ]
    blob = json.loads(_build_rc_data(scoreboard, history))
    attacker = next(a for a in blob["turns"][0]["actions"] if a["agent"] == "A")
    assert attacker["betrayed_helper"] is True
    assert attacker["betrayal_bonus"] == 4
    # The HURT's own delta stays the victim's -4 (the +4 is on betrayal_bonus).
    assert attacker["delta"] == -4
