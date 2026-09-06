"""Ratchet: a NEW copy of a tests/conftest.py fixture must not slip in unnoticed.

tests/conftest.py is the declared home for `reset_db`, `engine`,
`session_factory`, `db`, `client`, `db_session`, and `app` — plus
`reset_pull_rate_limit` / `admin_settings` for the two one-line monkeypatches
several test files used to copy the whole `reset_db` fixture just to add. A
tests/test_*.py file that defines its own `@pytest.fixture` under one of those
first seven names is checked against the `[[test_fixtures]]` table in
one_home_verdicts.toml, the same ratchet shape as
tests/test_test_clients_have_one_home.py and
tests/test_test_helpers_have_one_home.py.
"""

from __future__ import annotations

import ast
import pathlib

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

FIXTURE_NAMES = frozenset(
    {"reset_db", "engine", "session_factory", "db", "client", "db_session", "app"}
)
REDIRECT_MESSAGE = (
    "use the fixture from tests/conftest.py, or compose it with "
    "reset_pull_rate_limit / admin_settings"
)


def _is_pytest_fixture_decorator(dec: ast.expr) -> bool:
    """True for `@pytest.fixture` or `@pytest.fixture(...)`."""
    node = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(node, ast.Attribute) and node.attr == "fixture"


def scan_test_fixtures(root: pathlib.Path) -> set[tuple[str, str]]:
    """{(relative file path, fixture name)} for every tests/test_*.py file
    under `root` that defines its own `@pytest.fixture` under one of
    FIXTURE_NAMES."""
    hits: set[tuple[str, str]] = set()
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return hits
    for path in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in FIXTURE_NAMES:
                continue
            if any(_is_pytest_fixture_decorator(d) for d in node.decorator_list):
                hits.add((str(path.relative_to(root)), node.name))
    return hits


def _recorded_test_fixtures(root: pathlib.Path) -> dict[tuple[str, str], dict]:
    verdicts = _load_verdicts(root)
    return {(entry["file"], entry["name"]): entry for entry in verdicts.get("test_fixtures", [])}


def find_unrecorded_or_returned(
    current: set[tuple[str, str]], recorded: dict[tuple[str, str], dict]
) -> list[str]:
    """Current hits with no recorded entry, or whose recorded entry says
    "merged" (the copy was supposed to be gone)."""
    problems = []
    for file, name in sorted(current):
        entry = recorded.get((file, name))
        if entry is None:
            problems.append(f"{file}: {name}: {REDIRECT_MESSAGE}")
        elif entry["verdict"] == "merged":
            problems.append(f"{file}: {name}: was merged; a copy is back. {REDIRECT_MESSAGE}")
    return problems


def find_stale(
    current: set[tuple[str, str]], recorded: dict[tuple[str, str], dict]
) -> list[str]:
    """Recorded "unjudged"/"leave" entries whose fixture no longer exists in
    that file. ("merged" entries are expected to be gone — that is not
    stale.)"""
    stale = []
    for (file, name), entry in sorted(recorded.items()):
        if entry["verdict"] in ("unjudged", "leave") and (file, name) not in current:
            stale.append(f"{file}: {name}: recorded but no longer defined — delete the entry")
    return stale


def test_every_test_fixture_copy_is_recorded() -> None:
    problems = find_unrecorded_or_returned(
        scan_test_fixtures(REPO_ROOT), _recorded_test_fixtures(REPO_ROOT)
    )
    assert not problems, "\n".join(problems)


def test_no_stale_test_fixture_entries() -> None:
    stale = find_stale(scan_test_fixtures(REPO_ROOT), _recorded_test_fixtures(REPO_ROOT))
    assert not stale, "\n".join(stale)


def test_new_fixture_copy_fails_the_ratchet(tmp_path: pathlib.Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "import pytest\n\n\n@pytest.fixture\nasync def reset_db():\n    ...\n"
    )
    problems = find_unrecorded_or_returned(scan_test_fixtures(tmp_path), recorded={})
    assert len(problems) == 1
    assert "tests/test_example.py: reset_db" in problems[0]
    assert REDIRECT_MESSAGE in problems[0]
