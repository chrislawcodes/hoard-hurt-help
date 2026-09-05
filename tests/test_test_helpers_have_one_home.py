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

TESTS_DIR = Path(__file__).resolve().parent
VERDICTS_PATH = TESTS_DIR.parent / "one_home_verdicts.toml"
EXCLUDED_FILES = {"factories.py", "conftest.py"}
NAME_RE = re.compile(r"^_?(seed|make|create)_")

REDIRECT_MESSAGE = (
    "use tests/factories.py (make_match, make_user, make_agent, make_bot, "
    "seat_player, make_turn) or add the option there"
)


def _current_duplicate_helpers() -> dict[str, list[str]]:
    """Seeding-shaped module-level def/async def names defined in 2+ tests/*.py files."""
    hits: dict[str, list[str]] = defaultdict(list)
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.name in EXCLUDED_FILES:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and NAME_RE.match(
                node.name
            ):
                hits[node.name].append(f"tests/{path.name}")
    return {name: files for name, files in hits.items() if len(files) >= 2}


def _recorded_test_helpers() -> dict[str, dict]:
    with VERDICTS_PATH.open("rb") as f:
        data = tomllib.load(f)
    return {entry["name"]: entry for entry in data.get("test_helpers", [])}


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
