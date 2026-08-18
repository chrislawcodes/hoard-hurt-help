"""Smoke tests for the duplicate-rule scanner.

Deliberately NOT pinning what it finds. The findings are a function of the live
codebase and change with every refactor; asserting them would make the test a
maintenance tax that says nothing about whether the scanner works. What matters
is that it runs over the real tree, returns structured findings, and never gates
a build — it reports candidates a human has to judge.
"""

from __future__ import annotations

import pathlib

from scripts.find_duplicate_rules import (
    CHECKS,
    check_duplicate_function_names,
    check_guard_gaps,
    check_repeated_predicates,
    main,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_every_check_runs_over_the_real_tree() -> None:
    """A scanner that crashes on the codebase it ships with is worse than none."""
    for _title, check in CHECKS:
        findings = check(REPO_ROOT)
        assert isinstance(findings, list)
        assert all(isinstance(f, str) for f in findings)


def test_it_reports_and_never_gates(capsys) -> None:
    """Exit 0 always, by design.

    Each hit needs a human to ask "re-derived, or delegated to one shared
    function?" — delegation looks identical from the outside and is the goal. Wired
    as a CI gate, the false positives would train everyone to ignore it.
    """
    assert main() == 0
    out = capsys.readouterr().out
    assert "candidates" in out
    assert "not verdicts" in out


def test_guard_check_ignores_models_with_no_guard_fields() -> None:
    """The guard check keys off a known set of "...unless" columns, so a model
    without one must contribute nothing rather than raising."""
    findings = check_guard_gaps(REPO_ROOT)
    assert all("::" not in f for f in findings)


def test_function_name_check_needs_three_definitions() -> None:
    """Two files sharing a name is usually deliberate (the MCP tools re-expose the
    engine's play functions; each game has its own `_now`). Three is the smell."""
    findings = check_duplicate_function_names(REPO_ROOT)
    for line in findings:
        if "defined in" in line:
            count = int(line.split("defined in ")[1].split(" ")[0])
            assert count >= 3, f"threshold leaked a 2-file name: {line}"


def test_predicate_check_skips_trivial_lookups() -> None:
    """Fewer than three filtered fields is a plain join, not a rule."""
    findings = check_repeated_predicates(REPO_ROOT)
    for line in findings:
        if line.startswith("["):
            fields = line.split("]")[0].count(",") + 1
            assert fields >= 3, f"trivial predicate leaked through: {line}"
