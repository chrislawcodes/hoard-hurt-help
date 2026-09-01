"""Guard the one copy of the plain-language writing rules.

The rules live in `~/.claude/rules/plain-language.md`. The every-turn reminder
hook reads that file directly, so it cannot drift. This repo's output style is
the one place that must hold a *copy*: the repo is shared, and a checked-in file
cannot read a file in someone's home folder.

Per CLAUDE.md's "Two forms need a test", this checks the copy against the source.
The source lives outside the repo, so that comparison can only run on a machine
that has it — in CI it skips, loudly. The second test needs no source file and
runs everywhere: it checks the repo copy still carries every rule, so a CI run
catches a rule being deleted even when it cannot catch a rule being reworded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
STYLE_FILE: Path = REPO_ROOT / ".claude" / "output-styles" / "plain-language.md"
SOURCE_FILE: Path = Path.home() / ".claude" / "rules" / "plain-language.md"

_BLOCK = re.compile(r"<!-- RULES:START -->(.*?)<!-- RULES:END -->", re.S)

# Every rule the copy must still carry, as a phrase that survives light rewording.
REQUIRED_RULES: tuple[str, ...] = (
    "200 words",           # cap on reply length
    "25 words",            # cap on sentence length
    "shortest word",       # word choice
    "don't name the idea",  # saying the idea, not its name
    "say what happened",   # replace jargon rather than explain it
    "mashing two words",   # no invented compounds
    "invent acronyms",     # no invented shorthand
    "trade-off or risk",   # name risks plainly
    "everyday verbs",      # plain verbs
)


def _rules_block(path: Path) -> str:
    """Return the marked rules block, with whitespace flattened for comparison."""
    match = _BLOCK.search(path.read_text())
    if match is None:
        raise AssertionError(f"no RULES block markers found in {path}")
    return " ".join(match.group(1).split())


def test_repo_copy_carries_every_rule() -> None:
    """The repo's copy must still list every rule. Runs everywhere, including CI."""
    block = _rules_block(STYLE_FILE).lower()
    missing = [rule for rule in REQUIRED_RULES if rule.lower() not in block]
    assert not missing, (
        f"{STYLE_FILE.name} has lost these rules: {missing}. "
        f"Restore them from {SOURCE_FILE}."
    )


def test_repo_copy_matches_the_source() -> None:
    """The copy must match the source word for word, where the source exists."""
    if not SOURCE_FILE.exists():
        pytest.skip(
            f"no source at {SOURCE_FILE} — this check only runs on a machine that "
            "has the global rules file (CI does not). "
            "test_repo_copy_carries_every_rule still guards the copy here."
        )
    assert _rules_block(STYLE_FILE) == _rules_block(SOURCE_FILE), (
        f"{STYLE_FILE} has drifted from {SOURCE_FILE}. "
        "Edit the source, then copy its RULES block back into the style file."
    )
