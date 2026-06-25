"""Unit tests for `apply_inround_turn` — the viewer's running-score mirror.

Pure function (dict in, dict out). It approximates `resolve_turn` for lead
tracking / win-prob display, including the betrayal sting: a HURT against a
player who HELPs the attacker this same turn lands for BETRAYAL_HURT_POINTS.
"""

from __future__ import annotations

from app.games.hoard_hurt_help.scoring import apply_inround_turn


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


def test_mirror_betrayal_sting_is_eight():
    """HURTing a player who HELPs you this same turn drops them by 8."""
    out = apply_inround_turn(
        {"A": 0, "B": 10},
        [
            {"action": "HURT", "agent_id": "A", "target_id": "B"},
            {"action": "HELP", "agent_id": "B", "target_id": "A"},
        ],
    )
    assert out == {"A": 4, "B": 2}  # A: +4 from B's help; B: 10 - 8 sting


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
