"""The agent-facing rules text must describe betraying a helper and stay in
sync with the payoff constants — agents can't strategize around an unstated rule.
"""

from __future__ import annotations

from app.games.hoard_hurt_help.rules import (
    BETRAYAL_BONUS,
    GAME_RULES_TEXT,
    HELP_POINTS,
    HURT_POINTS,
    MUTUAL_HELP_FLOOR,
    make_game_rules_text,
)


def test_rules_text_documents_betraying_a_helper():
    assert "Betraying a helper" in GAME_RULES_TEXT
    # The 8/4 split must be stated: attacker nets +8 (help + bonus), victim -4.
    attacker_net = HELP_POINTS + BETRAYAL_BONUS
    assert f"+{attacker_net}" in GAME_RULES_TEXT  # attacker's net gain
    assert f"-{HURT_POINTS}" in GAME_RULES_TEXT  # victim takes the normal HURT
    # The attacker's bonus equals the base HURT under 8/4 — that's intentional.
    assert BETRAYAL_BONUS == HURT_POINTS


def test_rules_text_is_versioned_v5():
    assert "(v5)" in GAME_RULES_TEXT


def test_rules_text_documents_mutual_help_decay():
    assert "Mutual-help decays" in GAME_RULES_TEXT
    # The floor shown to agents must match the constant.
    assert f"+{MUTUAL_HELP_FLOOR} each" in GAME_RULES_TEXT


def test_custom_round_counts_keep_betraying_a_helper():
    text = make_game_rules_text(total_rounds=10, turns_per_round=10)
    assert "Betraying a helper" in text
    assert "**10 rounds**" in text
