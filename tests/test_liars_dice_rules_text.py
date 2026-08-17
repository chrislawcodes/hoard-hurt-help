"""The Liar's Dice rules text must describe the win condition the code enforces.

`LiarsDice.is_match_over` returns True as soon as one player still has dice — the
match ends on **elimination**, whenever that happens. But `total_rounds=64` and
`turns_per_round=256` are stored on the match like any other game's, and the
rules text used to read them out as the game's shape:

    **Game length:** **64 rounds**, each with **256 turns** (16384 turns total).
    After **64 rounds**, the last surviving player wins the match.

Both sentences are false. Those two numbers are safety ceilings — set far above
any real game so a stuck match cannot run forever — and the loop reaches them
only if elimination somehow never happens. Worse, the same text already said the
right thing two sections earlier ("The game ends when only 1 player remains"),
so an agent was handed a document that contradicted itself.

It matters because match length is a strategic input here: how hard you bluff in
Liar's Dice depends on how close elimination is. An agent told it has 16,384
turns to work with plays a different game from the one it is in.
"""

from __future__ import annotations

import pytest

from app.games import get as get_game_module
from app.games.liars_dice.game import LiarsDice
from app.models import PlayerState

LD = "liars-dice"


@pytest.fixture
def rules() -> str:
    return get_game_module(LD).semantic_rules_text()


def test_the_text_states_the_real_win_condition(rules: str) -> None:
    assert "match ends when one player still has dice" in rules
    assert "no round limit to play toward" in rules


def test_the_text_does_not_sell_the_ceilings_as_the_game_length(rules: str) -> None:
    cfg = get_game_module(LD).config_defaults()
    # The exact sentences that were wrong.
    assert f"**{cfg.total_rounds} rounds**, each with" not in rules
    assert f"After **{cfg.total_rounds} rounds**, the last surviving player wins" not in rules
    # And the headline "how long is this" answer is the honest one.
    assert "**Game length:** not fixed" in rules


def test_the_ceilings_are_still_disclosed_and_labelled_as_ceilings(rules: str) -> None:
    # An agent should still know a cutoff exists — just not mistake it for the
    # finish line. The numbers come from config_defaults, never a literal.
    cfg = get_game_module(LD).config_defaults()
    assert "Safety ceilings" in rules
    assert f"after {cfg.turns_per_round} turns" in rules
    assert f"after {cfg.total_rounds} rounds" in rules


def test_the_text_does_not_contradict_itself_about_how_the_match_ends(
    rules: str,
) -> None:
    # The elimination section and the structure section must agree. Before, one
    # said "when only 1 player remains" and the other "after 64 rounds".
    assert "The game ends when only 1 player remains" in rules
    assert "the last surviving player wins the match" not in rules


async def test_the_match_really_does_end_long_before_the_ceiling(reset_db) -> None:
    """The claim the text now makes, checked against the code that enforces it.

    Not a reading of `is_match_over` — this seats a table, eliminates everyone but
    one player, and asserts the match is over while `rounds_awarded` is still
    nowhere near `total_rounds`. That gap is exactly why calling those numbers
    the "game length" was wrong.
    """
    from tests.test_liars_dice_module import _seed_match

    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(db, match_id="M_LD_END")
        assert await module.is_match_over(db, match) is False

        # Knock two of the three players out by emptying their dice.
        for player in players[1:]:
            state = await db.get(PlayerState, {"match_id": match.id, "player_id": player.id})
            state.state_json = {"dice": [], "dice_count": 0}
        await db.commit()

        assert await module.is_match_over(db, match) is True
        # ...and we are nowhere near the ceiling the old text called the length.
        assert match.rounds_awarded < match.total_rounds
        assert match.total_rounds == module.config_defaults().total_rounds


def test_bare_rules_text_uses_this_games_own_ceilings() -> None:
    # The count arguments defaulted to 7/7 — a shape Liar's Dice has never run.
    # No production caller hit it (the one caller passes config_defaults), but it
    # was a trap for the next one.
    module = get_game_module(LD)
    cfg = module.config_defaults()
    text = module.semantic_rules_text()
    assert f"after {cfg.turns_per_round} turns" in text
    assert f"after {cfg.total_rounds} rounds" in text
    assert "after 7 turns" not in text
    assert "after 7 rounds" not in text
