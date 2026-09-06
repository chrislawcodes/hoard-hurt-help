"""Ratchet: a NEW copy of a tests/ seeding helper must not slip in unnoticed.

Same idea as tests/test_one_home_ratchet.py, scoped to tests/*.py instead of
app/. tests/factories.py is the declared home for match/user/agent/etc.
seeding; a module-level `def`/`async def` elsewhere under tests/ matching
`^_?(seed|make|create)_` and defined in two or more files is checked against
the `[[test_helpers]]` table in one_home_verdicts.toml.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
VERDICTS_PATH = TESTS_DIR.parent / "one_home_verdicts.toml"
EXCLUDED_FILES = {"factories.py", "conftest.py"}
NAME_RE = re.compile(r"^_?(seed|make|create)_")

# Every merged test_helpers verdict in one_home_verdicts.toml was set as part
# of PR D, which introduced that table (see the file's own section header).
# The table has no per-entry `pr` field the way [[function_names]] does.
MERGED_PR = "PR D"

REDIRECT_MESSAGE = (
    "use tests/factories.py (make_match, make_user, make_agent, make_bot, "
    "seat_player, make_turn) or add the option there"
)


def _all_test_helper_definitions(tests_dir: Path = TESTS_DIR) -> dict[str, list[str]]:
    """Seeding-shaped module-level def/async def names, every tests/*.py file
    that defines one, count >= 1.

    _current_duplicate_helpers() filters this down to names in 2+ files — the
    right bar for a *new* duplicate. But a "merged" verdict claims a name is
    gone for good, and a name back in exactly ONE file is invisible to that 2+
    filter. Checking "merged" against this wider, unfiltered set is what
    catches it.
    """
    hits: dict[str, list[str]] = defaultdict(list)
    for path in sorted(tests_dir.glob("*.py")):
        if path.name in EXCLUDED_FILES:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and NAME_RE.match(
                node.name
            ):
                hits[node.name].append(f"tests/{path.name}")
    return dict(hits)


def _current_duplicate_helpers(tests_dir: Path = TESTS_DIR) -> dict[str, list[str]]:
    """Seeding-shaped module-level def/async def names defined in 2+ tests/*.py files."""
    return {
        name: files
        for name, files in _all_test_helper_definitions(tests_dir).items()
        if len(files) >= 2
    }


def _recorded_test_helpers(verdicts_path: Path = VERDICTS_PATH) -> dict[str, dict[str, Any]]:
    with verdicts_path.open("rb") as f:
        data = tomllib.load(f)
    return {entry["name"]: entry for entry in data.get("test_helpers", [])}


def _merged_helper_problems(
    recorded: dict[str, dict[str, Any]], everywhere: dict[str, list[str]]
) -> list[str]:
    """For every entry with verdict "merged", fail if the name shows up in any
    tests/*.py file at all — not just in 2+ files."""
    problems: list[str] = []
    for name, entry in sorted(recorded.items()):
        if entry["verdict"] != "merged":
            continue
        back = sorted(everywhere.get(name, []))
        if back:
            problems.append(
                f"{name} was merged in {MERGED_PR}; a copy is back in {back[0]}. "
                f"Import it from {entry['home']}."
            )
    return problems


def test_every_duplicate_helper_is_recorded_with_its_current_files() -> None:
    current = _current_duplicate_helpers()
    recorded = _recorded_test_helpers()

    problems = []
    for name, files in sorted(current.items()):
        entry = recorded.get(name)
        if entry is None:
            problems.append(f"{name} (new): {REDIRECT_MESSAGE}")
            continue
        if entry["verdict"] == "merged":
            problems.append(f"{name}: recorded verdict is 'merged' but still defined in {files}")
            continue
        if sorted(entry["files"]) != sorted(files):
            problems.append(f"{name} (new file, now {files}): {REDIRECT_MESSAGE}")

    assert not problems, "\n".join(problems)


def test_no_stale_test_helper_entries() -> None:
    current = _current_duplicate_helpers()
    recorded = _recorded_test_helpers()

    stale = [
        name
        for name, entry in sorted(recorded.items())
        if entry["verdict"] in ("unjudged", "leave") and name not in current
    ]

    assert not stale, "\n".join(f"{name}: stale: delete or set merged" for name in stale)


def test_merged_test_helpers_have_not_come_back() -> None:
    """A "merged" verdict says the copies are gone for good. Check that against
    every definition in the repo, not just the ones the 2+ filter still sees —
    a merged name back in exactly one file is invisible to that filter alone."""
    everywhere = _all_test_helper_definitions()
    recorded = _recorded_test_helpers()
    problems = _merged_helper_problems(recorded, everywhere)
    assert not problems, "\n".join(problems)


def test_merged_verdict_catches_a_name_back_in_a_single_file(tmp_path: Path) -> None:
    """Pins the bug this ratchet exists to catch: a merged helper reappearing in
    exactly ONE file is invisible to _current_duplicate_helpers' 2+ filter, but
    must not be invisible to the merged-verdict check.

    Proven on main before this fix: `async def _seed_user(reset_db): ...` in a
    single new tests/*.py file passed test_test_helpers_have_one_home.py even
    though its verdict is "merged".
    """
    (tmp_path / "test_zz.py").write_text(
        "async def _seed_user(reset_db):\n    ...\n"
    )

    # The 2+ filter alone misses it — that's the bug.
    assert "_seed_user" not in _current_duplicate_helpers(tmp_path)

    recorded = {
        "_seed_user": {"verdict": "merged", "home": "tests/factories.py"},
        "_seed_match": {"verdict": "merged", "home": "tests/factories.py"},
    }
    everywhere = _all_test_helper_definitions(tmp_path)
    problems = _merged_helper_problems(recorded, everywhere)
    assert problems == [
        "_seed_user was merged in PR D; a copy is back in tests/test_zz.py. "
        "Import it from tests/factories.py."
    ]
