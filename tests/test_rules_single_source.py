"""Every surface that tells an agent the rules must tell it the SAME rules.

There are four of them, reached by different code paths:

* the HTTP turn payload's ``static.rules`` and ``static.base_prompt``
  (``app/engine/agent_play_reads``) — what the connector primes a session with,
* the MCP ``get_instructions`` block (``mcp_server/mcp_tools``) — what a direct
  MCP client reads,
* the public ``/games/{game}/agent-instructions`` page — what a human reads
  before writing a strategy.

They agreed only by convention, and the convention broke: the page and every
other "mode not supplied" caller fell through to decay while real matches were
created flat_6, so the page advertised a decaying +8 pact against a game paying
a flat +6. Nothing failed — an agent author simply optimised for a rule that no
longer existed.

These tests pin the two invariants that make the surfaces agree:

1. asked about a specific match, all of them state that match's rules;
2. asked with nothing specified, all of them state the SHIPPED rules — the ones
   `config_defaults()` and `DEFAULT_MUTUAL_HELP_MODE` hand a brand-new match.
"""

from __future__ import annotations

import pytest

from app.games import get as get_game_module
from app.games.base import GameError
from app.games.hoard_hurt_help.rules import (
    ACTIONS,
    SCORE_FLOOR,
    DEFAULT_MUTUAL_HELP_MODE,
    DEFAULT_TOTAL_ROUNDS,
    DEFAULT_TURNS_PER_ROUND,
    MutualHelpMode,
    mutual_help_value,
)
from app.models.match import Match

PD = "hoard-hurt-help"


def _match(**kwargs) -> Match:
    """A Match carrying only the fields the rules text reads."""
    defaults = {
        "id": "M_rules",
        "game": PD,
        "total_rounds": DEFAULT_TOTAL_ROUNDS,
        "turns_per_round": DEFAULT_TURNS_PER_ROUND,
        "mutual_help_mode": DEFAULT_MUTUAL_HELP_MODE.value,
    }
    return Match(**{**defaults, **kwargs})


def _pact_line(text: str) -> str:
    """The one line of the rules that varies with the mutual-help mode."""
    lines = [ln for ln in text.splitlines() if "Mutual-help" in ln]
    assert lines, f"no mutual-help line in rules text:\n{text}"
    return lines[0]


# --- 1. Nothing specified means the shipped rules -------------------------


def test_bare_rules_text_is_the_shipped_match_length_and_mode():
    module = get_game_module(PD)
    cfg = module.config_defaults()
    bare = module.semantic_rules_text()

    assert f"**{cfg.total_rounds} rounds**" in bare
    assert f"**{cfg.turns_per_round} turns**" in bare
    assert f"net +{mutual_help_value(DEFAULT_MUTUAL_HELP_MODE, 0)}" in _pact_line(bare)


def test_bare_agent_base_prompt_is_the_shipped_rules():
    # This is the exact call /games/{game}/agent-instructions makes. It rendered
    # the wrong mutual-help rule for as long as the mode default said "decay".
    module = get_game_module(PD)
    page = module.agent_base_prompt(
        your_agent_id="<your agent ID>",
        all_agent_ids=["<your agent ID>", "<other agent IDs>"],
    )
    shipped = module.semantic_rules_text()
    assert shipped.rstrip() in page


def test_bare_rules_match_config_defaults():
    # Hoard-Hurt-Help's rules text must never quote a match length the game does
    # not run. Scoped to this game on purpose: each game module owns its own rules
    # and is checked by its own tests, so nothing here reaches across into
    # another title's text.
    module = get_game_module(PD)
    cfg = module.config_defaults()
    for text in (module.rules_text(), module.semantic_rules_text()):
        assert f"**{cfg.total_rounds} rounds**" in text
        assert f"**{cfg.turns_per_round} turns**" in text


# --- 2. Given a match, every surface states THAT match's rules -------------


@pytest.mark.parametrize(
    "mode", [m for m in MutualHelpMode if m is not MutualHelpMode.NO_REPEATS]
)
def test_all_agent_facing_surfaces_agree_for_one_match(mode: MutualHelpMode):
    # NO_REPEATS is excluded only because its payout depends on the previous
    # turn, so it has no single headline number to compare.
    module = get_game_module(PD)
    match = _match(total_rounds=4, turns_per_round=6, mutual_help_mode=mode.value)

    http_rules = module.rules_text_for_match(match)
    mcp_rules = module.semantic_rules_text_for_match(match)
    base_prompt = module.agent_base_prompt_for_match(
        match, your_agent_id="AI_1", all_agent_ids=["AI_1", "AI_2"]
    )

    expected_pact = f"net +{mutual_help_value(mode, 0)}"
    for name, text in (
        ("http rules", http_rules),
        ("mcp rules", mcp_rules),
        ("base prompt", base_prompt),
    ):
        assert "**4 rounds**" in text, name
        assert "**6 turns**" in text, name
        assert expected_pact in _pact_line(text), (name, mode)

    # The MCP block is the HTTP rules minus the connector's JSON protocol, and
    # the base prompt embeds that same block — not a re-description of it.
    assert mcp_rules.rstrip() in http_rules
    assert mcp_rules.rstrip() in base_prompt


def test_a_match_row_with_no_mode_reads_as_the_rule_it_was_played_under():
    # Rows predating the mode switch have mutual_help_mode NULL and every one of
    # them ran decay. Re-reading such a row as today's default would restate the
    # history of a finished match, so the NULL fallback stays decay even as the
    # shipped default moves.
    module = get_game_module(PD)
    legacy = _match(mutual_help_mode=None)
    decayed = module.semantic_rules_text(mutual_help_mode=MutualHelpMode.DECAY.value)
    assert _pact_line(module.semantic_rules_text_for_match(legacy)) == _pact_line(
        decayed
    )


def test_the_move_vocabulary_is_stated_once():
    # It used to be a tuple in `action_names()` and a separate set literal driving
    # move validation — two lists of the same three words, free to disagree.
    module = get_game_module(PD)
    assert module.action_names() == ACTIONS
    for action in ACTIONS:
        module.validate_move(
            {"action": action, "target_id": None if action == "HOARD" else "AI_2"},
            your_agent_id="AI_1",
            all_agent_ids=["AI_1", "AI_2"],
        )
    with pytest.raises(GameError):
        module.validate_move(
            {"action": "SHARE"}, your_agent_id="AI_1", all_agent_ids=["AI_1", "AI_2"]
        )
    # And the rules text names exactly those moves, no more and no fewer.
    text = module.semantic_rules_text()
    for action in ACTIONS:
        assert f"**{action}" in text, action


def test_the_score_floor_is_stated_once():
    # The floor was a bare `0` in two code paths and the word "0" in the prose.
    assert f"clipped at {SCORE_FLOOR}" in get_game_module(PD).semantic_rules_text()


@pytest.mark.parametrize(
    "rounds,turns", [(3, 3), (4, 9), (20, 20), (1, 1), (DEFAULT_TOTAL_ROUNDS, 20)]
)
def test_a_custom_length_renders_without_leaking_the_default(rounds: int, turns: int):
    # The counts used to be rendered at the defaults and then string-replaced into
    # place, which meant rewording any of five sentences would silently stop
    # rewriting it and hand a custom-length match the DEFAULT numbers. They are
    # interpolated directly now; this pins that no default leaks through.
    text = get_game_module(PD).semantic_rules_text(rounds, turns)
    assert f"**{rounds} rounds**" in text
    assert f"**{turns} turns**" in text
    assert f"({rounds * turns} turns total)" in text
    assert f"after turn {turns} " in text
    assert f"after all {rounds} rounds" in text


def test_a_match_keeps_its_own_rules_when_the_shipped_default_moves():
    # Each match stores its length and mode, so changing what NEW matches get
    # never rewrites the rules an in-flight or finished match is read under.
    module = get_game_module(PD)
    off_default = _match(
        total_rounds=DEFAULT_TOTAL_ROUNDS + 1,
        turns_per_round=DEFAULT_TURNS_PER_ROUND + 1,
        mutual_help_mode=MutualHelpMode.FLAT_8.value,
    )
    text = module.semantic_rules_text_for_match(off_default)
    assert f"**{DEFAULT_TOTAL_ROUNDS + 1} rounds**" in text
    assert f"**{DEFAULT_TURNS_PER_ROUND + 1} turns**" in text
    assert f"net +{mutual_help_value(MutualHelpMode.FLAT_8, 0)}" in _pact_line(text)
