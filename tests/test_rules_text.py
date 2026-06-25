"""The agent-facing rules text must describe betraying a helper and stay in
sync with the payoff constants — agents can't strategize around an unstated rule.
"""

from __future__ import annotations

from app.games.hoard_hurt_help.rules import (
    BETRAYAL_HURT_POINTS,
    GAME_RULES_TEXT,
    HURT_POINTS,
    make_game_rules_text,
)


def test_rules_text_documents_betraying_a_helper():
    assert "Betraying a helper" in GAME_RULES_TEXT
    # The betrayal magnitude shown must match the constant, and differ from base HURT.
    assert f"-{BETRAYAL_HURT_POINTS}" in GAME_RULES_TEXT
    assert BETRAYAL_HURT_POINTS != HURT_POINTS


def test_rules_text_is_versioned_v3():
    assert "(v3)" in GAME_RULES_TEXT


def test_custom_round_counts_keep_betraying_a_helper():
    text = make_game_rules_text(total_rounds=10, turns_per_round=10)
    assert "Betraying a helper" in text
    assert "**10 rounds**" in text
