"""Docs that describe how the game plays TODAY must agree with the code.

Prose is the one place a rule can be restated without anything noticing. When the
match length moved, four docs kept their old numbers — and two of them
(`README.md`, the `game-design` skill's pattern reference) had been wrong since
*before* that change, advertising a 10x10 match the game had not run in a long
time. A person reading them to write a strategy, or an agent handed the
`game-design` skill, is being told the wrong game.

Two guards, both deliberately conservative — the goal is zero false failures on
prose, same as `test_arch_doc_paths.py`:

1. **No current-behaviour doc may state a match length that isn't the shipped
   one.** Any "N rounds x M turns" phrasing in those files must match the
   constants.
2. **The docs that are supposed to state it, do.** A doc silently dropping the
   sentence would pass guard 1 by saying nothing at all.

HISTORY_DOCS are excluded on purpose. A changelog entry, an incident write-up or
the failure-archaeology chronicle *should* say "5 rounds of 7 turns" — that is a
record of what was true then, and rewriting it would destroy the thing those
files exist for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.games import get as get_game_module
from app.games.base import BaseGameModule
from app.games.hoard_hurt_help.rules import (
    DEFAULT_TOTAL_ROUNDS,
    DEFAULT_TURNS_PER_ROUND,
    RULES_VERSION,
)

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

# Docs describing how the game plays right now. Add a doc here when it starts
# stating the match length; the guard then keeps it honest for free.
CURRENT_BEHAVIOUR_DOCS: tuple[str, ...] = (
    "README.md",
    "docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md",
    ".claude/skills/game-design/references/boardgame-design-patterns.md",
    # The platform design doc described a 10x10 match for a long time. Its
    # phrasing then ("Total rounds (10)") matched none of the patterns below,
    # so listing it only started guarding anything once it was rewritten to
    # say "7 rounds of 5 turns" in a shape this test can read.
    "docs/platform/AGENT_LUDUM_DESIGN.md",
)
# Deliberately NOT listed: another game's docs. Each title owns its own rules and
# its own docs; pulling a second game's design doc in here would couple it to
# Hoard-Hurt-Help's constants and make a change to one game fail the other's docs.

# Docs that record what USED to be true. Their old numbers are the point.
HISTORY_DOCS: tuple[str, ...] = (
    "STATUS.md",
    "experiments.md",
    "docs/operations/debugging-history.md",
    "docs/games/hoard-hurt-help/betray-helper-impact-review.md",
    ".claude/skills/failure-archaeology/SKILL.md",
    ".claude/skills/diagnostics-and-tooling/SKILL.md",
)

# The docs state the match length in two shapes, so the guard reads both. Paired
# ("7 rounds of 5 turns", "7x5 grid") and split ("**5 turns per round.**",
# "**7 rounds per match.**", "35 turns total").
_PAIRED = re.compile(
    r"(\d+)\s*rounds?\s*(?:of|x|×|by)?\s*(\d+)\s*turns?|(\d+)\s*[x×]\s*(\d+)\s+grid",
    re.IGNORECASE,
)
_TURNS_PER_ROUND = re.compile(r"(\d+)\s*turns?\s+per\s+round", re.IGNORECASE)
_ROUNDS_PER_MATCH = re.compile(r"(\d+)\s*rounds?\s+per\s+match", re.IGNORECASE)
_TURNS_TOTAL = re.compile(r"(\d+)\s*turns?\s+total", re.IGNORECASE)


def _claims(text: str) -> list[tuple[str, int, int]]:
    """Every match-length claim in the text, as (quoted phrase, actual, expected)."""
    shipped_total = DEFAULT_TOTAL_ROUNDS * DEFAULT_TURNS_PER_ROUND
    out: list[tuple[str, int, int]] = []
    for m in _PAIRED.finditer(text):
        rounds, turns = (
            (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        )
        out.append((m.group(0), int(rounds), DEFAULT_TOTAL_ROUNDS))
        out.append((m.group(0), int(turns), DEFAULT_TURNS_PER_ROUND))
    for pattern, expected in (
        (_TURNS_PER_ROUND, DEFAULT_TURNS_PER_ROUND),
        (_ROUNDS_PER_MATCH, DEFAULT_TOTAL_ROUNDS),
        (_TURNS_TOTAL, shipped_total),
    ):
        for m in pattern.finditer(text):
            out.append((m.group(0), int(m.group(1)), expected))
    return out


@pytest.mark.parametrize("doc", CURRENT_BEHAVIOUR_DOCS)
def test_current_docs_state_the_shipped_match_length(doc: str) -> None:
    path = REPO_ROOT / doc
    assert path.exists(), f"{doc} is listed as a current-behaviour doc but is missing"
    found = _claims(path.read_text())
    assert found, (
        f"{doc} is listed here because it states the match length, but no such "
        f"claim was found. Either restore the sentence or drop the file from "
        f"CURRENT_BEHAVIOUR_DOCS."
    )
    wrong = [(phrase, got, want) for phrase, got, want in found if got != want]
    assert not wrong, (
        f"{doc} is out of date. It says {wrong[0][0]!r} but the game ships "
        f"{DEFAULT_TOTAL_ROUNDS} rounds of {DEFAULT_TURNS_PER_ROUND} turns "
        f"({DEFAULT_TOTAL_ROUNDS * DEFAULT_TURNS_PER_ROUND} total). Update the "
        f"doc — or, if that sentence is describing history rather than today, "
        f"move the file to HISTORY_DOCS."
    )


@pytest.mark.parametrize("doc", HISTORY_DOCS)
def test_history_docs_are_left_alone(doc: str) -> None:
    # Not an assertion about their contents — just that the exclusion list names
    # real files, so a renamed doc can't silently fall out of the split and start
    # being treated as current.
    assert (REPO_ROOT / doc).exists(), (
        f"{doc} is excluded from the match-length guard but no longer exists. "
        f"Update HISTORY_DOCS, or the exclusion is now hiding a real doc."
    )


def test_rules_version_is_stated_once_and_matches_the_rules_text() -> None:
    # The version an agent is told and the rules it is handed arrive in the same
    # payload. They come from one constant so they cannot disagree.
    module = get_game_module("hoard-hurt-help")
    assert module.rules_version() == RULES_VERSION
    assert f"Official Rules ({RULES_VERSION})" in module.semantic_rules_text()


def test_a_game_without_its_own_rules_version_keeps_the_historical_default() -> None:
    # The hook is opt-in: a game that does not version its rules must keep
    # stamping exactly what the matches table defaulted to before the hook
    # existed, so adding it changed no other title's behaviour.
    class Unversioned(BaseGameModule):
        game_type = "unversioned"

    assert Unversioned().rules_version() == "v1"
