"""Every surface that tells an agent the rules must tell it the SAME rules.

There are three of them, reached by different code paths:

* the HTTP turn payload's ``static.base_prompt``
  (``app/engine/agent_play_reads``) — what the connector primes a session with,
* the MCP ``get_instructions`` block (``mcp_server/mcp_tools``) — what a direct
  MCP client reads,
* the public ``/games/{game}/agent-instructions`` page — what a human reads
  before writing a strategy.

All three now start from one builder, ``semantic_rules_text``. There used to be a
second, longer one (``rules_text``) that appended the connector's JSON response
protocol, and the turn payload carried both — so the connector shipped the whole
rulebook twice per turn. It was removed along with its per-match wrapper and both
games' ``make_rules_text``; these tests were rewritten onto the survivor.

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
from app.games.base import BaseGameModule, GameError
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
    text = module.semantic_rules_text()
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

    mcp_rules = module.semantic_rules_text_for_match(match)
    base_prompt = module.agent_base_prompt_for_match(
        match, your_agent_id="AI_1", all_agent_ids=["AI_1", "AI_2"]
    )

    expected_pact = f"net +{mutual_help_value(mode, 0)}"
    for name, text in (("mcp rules", mcp_rules), ("base prompt", base_prompt)):
        assert "**4 rounds**" in text, name
        assert "**6 turns**" in text, name
        assert expected_pact in _pact_line(text), (name, mode)

    # The base prompt — what the HTTP turn payload ships — embeds the MCP block
    # verbatim, rather than re-describing the rules in its own words.
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


def test_a_game_that_supplies_no_rules_fails_loud():
    # `semantic_rules_text` is the only rules text there is. It used to return ""
    # when a game did not override it, which was survivable while `rules_text` was
    # the loud one — but `rules_text` is gone. An empty string would now serve a
    # blank rulebook over MCP and embed a blank one in the base prompt, and nothing
    # anywhere would fail: the agent would simply be told nothing about the game.
    class RulelessGame(BaseGameModule):
        game_type = "ruleless"

    # Both entry points, because real serving reaches it through the wrapper.
    with pytest.raises(NotImplementedError, match="semantic_rules_text"):
        RulelessGame().semantic_rules_text()
    with pytest.raises(NotImplementedError, match="semantic_rules_text"):
        RulelessGame().semantic_rules_text_for_match(_match())


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


def test_the_move_payoffs_are_stated_once():
    """Every surface quoting a payoff must derive it, agent-facing or human-facing.

    The three viewer legend lines were typed literals ("+2 to yourself",
    "-4 to another; +4 ...") in two templates, and a bot chat line advertised a
    "mutual +8" the flat_6 default never paid. When the v6 payoffs moved
    HURT_POINTS 4 -> 8 and BETRAYAL_BONUS 4 -> 6, the engine changed and those
    lines did not — so spectators would have been shown one number while the
    resolver used another. They are built from the constants now; this is the
    guard. `hurt_legend` quotes the BONUS alone, matching the `+N betrayal` chip
    the turn feed renders, not the attacker's net for the turn.
    """
    from app.games.hoard_hurt_help.rules import (
        BETRAYAL_BONUS,
        HURT_TAKE_HELPER,
        HURT_TAKE_HOARDER,
        HELP_POINTS,
        HOARD_POT_POINTS,
        HURT_POINTS,
        help_legend,
        hoard_legend,
        hurt_legend,
    )

    assert f"+{HOARD_POT_POINTS}" in hoard_legend()
    assert f"-{HURT_POINTS} to another" in hurt_legend()
    # The legend now advertises every tier, because at v9 a HURT's payout depends
    # on what the TARGET was doing — a legend showing only the betrayal figure
    # would hide two rates the resolver actually pays.
    assert f"+{HELP_POINTS + BETRAYAL_BONUS} betraying a helper" in hurt_legend()
    assert f"+{HURT_TAKE_HELPER} off a helper" in hurt_legend()
    assert f"+{HURT_TAKE_HOARDER} off a hoarder" in hurt_legend()
    assert help_legend(DEFAULT_MUTUAL_HELP_MODE).startswith(f"+{HELP_POINTS} to another")

    # The agent-facing rules state the same payoffs the legends do.
    text = get_game_module(PD).semantic_rules_text()
    assert f"The target loses {HURT_POINTS} points" in text
    # ...and the tier table below it must state every rate the resolver pays, or
    # an agent reads a HURT as all-or-nothing the way it was before v9.
    assert f"**+{HURT_TAKE_HELPER}**" in text
    assert f"**+{HURT_TAKE_HOARDER}**" in text
    assert "Blocked." in text
    assert f"the target gains +{HELP_POINTS} points" in text
    assert f"splits +{HOARD_POT_POINTS} points" in text
    assert f"an extra +{BETRAYAL_BONUS} bonus" in text


def test_no_surface_hand_types_a_payoff_number():
    """No template or bot line may restate a payoff as a literal.

    Scans the two legend templates and the bot phrase bank for a bare payoff
    figure. This is the check that would have caught the stale "mutual +8" chat
    line, which was wrong from the moment flat_6 became the default — long before
    the v6 change — because nothing tied it to `mutual_help_value`.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    watched = [
        root / "app" / "templates" / "fragments" / "move_legend.html",
        root / "app" / "templates" / "fragments" / "robot_circle" / "_markup.html",
        root / "app" / "engine" / "bots" / "phrases.py",
    ]
    # A signed number next to a payoff word is the shape that drifts.
    pattern = re.compile(r"[+-]\d+\s*(?:to (?:you|another|yourself)|each)")
    for path in watched:
        offenders = pattern.findall(path.read_text())
        assert not offenders, f"{path.name} hand-types a payoff: {offenders}"


def test_the_hoard_pot_is_stated_once_and_splits_sanely():
    """HOARD's payout is a pot split between hoarders — derived everywhere.

    HOARD used to be a flat 2, which four separate places restated as a literal.
    It is a contested pot now, so its value changes turn to turn and a hand-typed
    number cannot be right. `hoard_share` is the single source; this pins its
    shape and that every surface quotes the pot rather than a fixed payout.
    """
    from app.games.hoard_hurt_help.rules import (
        HOARD_POT_POINTS,
        hoard_legend,
        hoard_share,
    )

    # A lone hoarder takes the pot; the slice never grows as company arrives.
    assert hoard_share(1) == HOARD_POT_POINTS
    shares = [hoard_share(n) for n in range(1, 21)]
    assert shares == sorted(shares, reverse=True), shares
    # Integer division only — scores must stay whole numbers.
    assert all(isinstance(v, int) for v in shares)
    # No hoarders means nothing is paid, and callers may ask without guarding.
    assert hoard_share(0) == 0
    # A split can never pay more than the pot, however the rounding falls.
    assert all(hoard_share(n) * n <= HOARD_POT_POINTS for n in range(1, 21))
    # The legend quotes the pot, not a per-player payout.
    assert str(HOARD_POT_POINTS) in hoard_legend()

    # And the agent-facing rules describe the split, not a flat number.
    text = get_game_module(PD).semantic_rules_text()
    assert f"splits +{HOARD_POT_POINTS} points" in text


def test_the_hurt_tiers_keep_their_design_order() -> None:
    """The tiers are tunable, but three relationships must hold.

    Each was derived before v9 shipped and each has a failure mode attached:

    1. A betrayal must out-pay every other tier, or the one attack that needs a
       relationship built first is worth less than one that needs nothing.
    2. HURT_TAKE_HELPER must stay BELOW a pact. At parity, attacking a cooperator
       pays exactly what cooperating pays AND damages them, so in a head-to-head
       endgame there is no reason not to swing and the final turn stops being a
       decision.
    3. Hoarding must be the smallest paying tier, and attacking someone who is
       themselves attacking must pay nothing — that ordering is what makes HURT a
       read on the target rather than a flat move.
    """
    from app.games.hoard_hurt_help.rules import (
        BETRAYAL_BONUS,
        DEFAULT_MUTUAL_HELP_MODE,
        HELP_POINTS,
        HURT_TAKE_HELPER,
        HURT_TAKE_HOARDER,
        hurt_take,
        mutual_help_value,
    )

    betrayal = HELP_POINTS + BETRAYAL_BONUS
    pact = mutual_help_value(DEFAULT_MUTUAL_HELP_MODE, 0)

    assert betrayal > HURT_TAKE_HELPER > HURT_TAKE_HOARDER > 0
    assert HURT_TAKE_HELPER < pact, (
        "at or above a pact, attacking a cooperator is free in a head-to-head"
    )
    assert hurt_take("HURT", target_helps_attacker=False) == 0

    # A betrayal is never shared, however many pile onto the same victim.
    assert (
        hurt_take("HELP", target_helps_attacker=True, attackers_on_target=4)
        == BETRAYAL_BONUS
    )
    # The grab tiers ARE shared.
    assert hurt_take(
        "HELP", target_helps_attacker=False, attackers_on_target=2
    ) == HURT_TAKE_HELPER // 2
