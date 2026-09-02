"""The game design doc states the payoffs in prose, so it must not drift.

`docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md` restates every payoff as
tables a reader can act on. That is a second written form of a rule
`app/games/hoard_hurt_help/rules.py` owns, and One Home Per Rule says two forms
need a test asserting they agree.

This guard exists because that doc drifted for three rules versions without
anything noticing: it carried the v8 table — HOARD a flat +2, HURT a flat -4 to
the target and nothing to the attacker — into a v11 game where HOARD splits an
8-point pot and a HURT pays the attacker a take priced off what the target did.
Nothing failed, because prose is the one place a rule can be restated with no
guard on it. `tests/test_rules_docs_current.py` already pins this doc's match
LENGTH; the payoffs had no equivalent.

Exact substring checks built from the constants, not a regex over prose — the
doc is written so each payoff appears once, in a form this file can construct.
A payoff change fails here with the string the doc should now carry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.games.hoard_hurt_help.rules import (
    BETRAYAL_BONUS,
    DEFAULT_MUTUAL_HELP_MODE,
    HELP_POINTS,
    HOARD_POT_POINTS,
    HURT_POINTS,
    HURT_TAKE_HELPER,
    HURT_TAKE_HOARDER,
    RULES_VERSION,
    mutual_help_value,
)

DESIGN_DOC: Path = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "games"
    / "hoard-hurt-help"
    / "HOARD_HURT_HELP_DESIGN.md"
)


def _expected() -> dict[str, str]:
    """Each payoff, as the exact text the design doc must carry for it."""
    pact = mutual_help_value(DEFAULT_MUTUAL_HELP_MODE, 0)
    return {
        "rules version": f"Rules version **{RULES_VERSION}**",
        "hoard pot": f"share of a +{HOARD_POT_POINTS} pot",
        "help payout": f"| Help [T] | 0 | +{HELP_POINTS} |",
        "hurt cost to target": f"see below | −{HURT_POINTS} |",
        "betrayal bonus": f"**+{BETRAYAL_BONUS} bonus**",
        "betrayal net": f"the attacker nets **+{HELP_POINTS + BETRAYAL_BONUS}**",
        "take off a helper": f"| HELPing someone else | +{HURT_TAKE_HELPER} |",
        "take off a hoarder": f"| HOARDing | +{HURT_TAKE_HOARDER} |",
        "default pact rate": (
            f"| `{DEFAULT_MUTUAL_HELP_MODE.value}` — **today's default for a "
            f"new match** | {pact} every time |"
        ),
    }


@pytest.fixture(scope="module")
def design_doc() -> str:
    assert DESIGN_DOC.exists(), f"{DESIGN_DOC} is missing — update or drop this guard"
    return DESIGN_DOC.read_text()


@pytest.mark.parametrize("label,phrase", sorted(_expected().items()))
def test_the_design_doc_states_the_shipped_payoff(
    design_doc: str, label: str, phrase: str
) -> None:
    assert phrase in design_doc, (
        f"The design doc no longer states the shipped {label}. rules.py says the "
        f"doc should read {phrase!r}. Update "
        f"docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md — a reader acts on "
        f"that table, and it went three rules versions out of date once already."
    )


def test_the_doc_does_not_still_carry_a_retired_payoff(design_doc: str) -> None:
    # The specific wrong sentences this guard was written to catch. They read as
    # plausible rules, which is why they survived three versions unnoticed.
    for retired in ("| Hoard | +2 | n/a |", "| Hurt [T] | 0 | −4 |"):
        assert retired not in design_doc, (
            f"The design doc has regained the retired v8 payoff {retired!r}."
        )
