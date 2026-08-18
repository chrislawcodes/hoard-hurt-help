"""The agent-facing rules text must describe betraying a helper and stay in
sync with the payoff constants — agents can't strategize around an unstated rule.
"""

from __future__ import annotations

from app.games import get as get_game_module
from app.games.hoard_hurt_help.rules import (
    BETRAYAL_BONUS,
    DEFAULT_MUTUAL_HELP_MODE,
    DEFAULT_TOTAL_ROUNDS,
    DEFAULT_TURNS_PER_ROUND,
    GAME_RULES_TEXT,
    HELP_POINTS,
    HURT_POINTS,
    LEGACY_MUTUAL_HELP_MODE,
    MUTUAL_HELP_BONUS,
    MUTUAL_HELP_FLOOR,
    MutualHelpMode,
    make_game_rules_text,
    mutual_help_value,
)


def test_rules_text_documents_betraying_a_helper():
    assert "Betraying a helper" in GAME_RULES_TEXT
    # The split must be stated: attacker nets help + bonus, victim takes -HURT.
    attacker_net = HELP_POINTS + BETRAYAL_BONUS
    assert f"+{attacker_net}" in GAME_RULES_TEXT  # attacker's net gain
    assert f"-{HURT_POINTS}" in GAME_RULES_TEXT  # victim takes the normal HURT
    # Betrayal must out-pay the best pact rate, or the knife is never worth it.
    # This is the point of the v6 payoffs; under v5 it tied FLAT_8 and only won
    # on rank. The bonus no longer equals the base HURT — do not re-couple them.
    assert attacker_net > HELP_POINTS + MUTUAL_HELP_BONUS


def test_rules_text_is_versioned_v6():
    assert "(v6)" in GAME_RULES_TEXT


def test_decay_rules_text_documents_the_decay_ladder_and_floor():
    # Asked for decay explicitly, the text describes decay — including the floor,
    # which must be the constant the resolver actually pays. This used to read
    # GAME_RULES_TEXT, which meant it only passed while decay happened to be what
    # an unspecified mode fell back to.
    text = make_game_rules_text(mode=MutualHelpMode.DECAY)
    assert "Mutual-help decays" in text
    assert f"+{MUTUAL_HELP_FLOOR} each" in text


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


def test_default_rules_text_is_the_shipped_mode():
    # GAME_RULES_TEXT is the text a brand-new match's agents get, so it must
    # describe the rule those matches are actually scored under. It was pinned to
    # decay while new matches ran flat_6, which is how the public
    # /agent-instructions page came to advertise a rule the game had left behind.
    assert GAME_RULES_TEXT == make_game_rules_text(mode=DEFAULT_MUTUAL_HELP_MODE)
    shipped_pact = mutual_help_value(DEFAULT_MUTUAL_HELP_MODE, 0)
    assert f"net +{shipped_pact}" in GAME_RULES_TEXT


def test_mode_defaults_are_not_collapsed():
    # Two different questions that must not be answered by one constant:
    #   - "the caller named no mode"   -> DEFAULT_MUTUAL_HELP_MODE (today's rule)
    #   - "the match row's mode is NULL" -> LEGACY_MUTUAL_HELP_MODE (its rule)
    # A NULL row predates the mode switch and was really played under decay, so
    # re-reading it as today's default would silently restate history.
    assert LEGACY_MUTUAL_HELP_MODE is MutualHelpMode.DECAY
    assert make_game_rules_text() == make_game_rules_text(mode=DEFAULT_MUTUAL_HELP_MODE)
    if DEFAULT_MUTUAL_HELP_MODE is not LEGACY_MUTUAL_HELP_MODE:
        assert make_game_rules_text() != make_game_rules_text(
            mode=LEGACY_MUTUAL_HELP_MODE
        )
