"""The agent-facing rules text must describe betraying a helper and stay in
sync with the payoff constants — agents can't strategize around an unstated rule.
"""

from __future__ import annotations

from app.games import get as get_game_module
from app.games.hoard_hurt_help.rules import (
    BETRAYAL_BONUS,
    DEFAULT_TOTAL_ROUNDS,
    DEFAULT_TURNS_PER_ROUND,
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
    assert "**10 turns**" in text
    assert "(100 turns total)" in text
    assert "after turn 10" in text
    assert "after all 10 rounds" in text


def test_default_rules_text_states_the_shipped_match_length():
    # The counts an agent reads must be the counts the scheduler actually runs.
    # A mismatch is invisible — the match still completes, agents just plan their
    # endgame for a turn that never comes.
    total = DEFAULT_TOTAL_ROUNDS
    per_round = DEFAULT_TURNS_PER_ROUND
    assert f"**{total} rounds**" in GAME_RULES_TEXT
    assert f"**{per_round} turns**" in GAME_RULES_TEXT
    assert f"({total * per_round} turns total)" in GAME_RULES_TEXT
    assert f"after turn {per_round}" in GAME_RULES_TEXT
    assert f"after all {total} rounds" in GAME_RULES_TEXT


def test_shipped_config_matches_the_rules_text_counts():
    # `config_defaults` is what a new match is created with; the constants above
    # are what the rules text is written from. They are the same two numbers.
    cfg = get_game_module("hoard-hurt-help").config_defaults()
    assert cfg.total_rounds == DEFAULT_TOTAL_ROUNDS
    assert cfg.turns_per_round == DEFAULT_TURNS_PER_ROUND
