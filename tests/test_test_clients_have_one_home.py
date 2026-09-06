"""Ratchet: a NEW test file building its own httpx/TestClient must not slip in
unnoticed.

tests/conftest.py's `client` fixture is the one home for an app-bound test
client (an httpx AsyncClient over ASGITransport). A tests/test_*.py file that
constructs AsyncClient(/TestClient( itself is checked against the
[[test_clients]] table in one_home_verdicts.toml, the same ratchet shape as
tests/test_one_home_ratchet.py.
"""

from __future__ import annotations

import pathlib
import re

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT_RE = re.compile(r"AsyncClient\(|TestClient\(")
REDIRECT_MESSAGE = "use the `client` fixture from tests/conftest.py"
# This file's own source mentions AsyncClient(/TestClient( in the regex above and in
# the tmp_path test below — text, not a client construction call site. Excluded the
# same way test_test_helpers_have_one_home.py excludes conftest.py/factories.py.
_SELF = pathlib.Path(__file__).name


def scan_test_clients(root: pathlib.Path) -> dict[str, int]:
    """{relative file path: count of own AsyncClient(/TestClient( construction}
    for every tests/test_*.py file under `root` that builds its own client."""
    counts: dict[str, int] = {}
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return counts
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == _SELF:
            continue
        n = len(CLIENT_RE.findall(path.read_text()))
        if n:
            counts[str(path.relative_to(root))] = n
    return counts


def _recorded_test_clients(root: pathlib.Path) -> dict[str, dict]:
    verdicts = _load_verdicts(root)
    return {entry["file"]: entry for entry in verdicts.get("test_clients", [])}


def find_new_or_risen(current: dict[str, int], recorded: dict[str, dict]) -> list[str]:
    """Files with no recorded entry, or with more client construction than recorded."""
    problems = []
    for file, count in sorted(current.items()):
        entry = recorded.get(file)
        if entry is None:
            problems.append(f"{file} ({count}): {REDIRECT_MESSAGE}")
        elif count > entry["count"]:
            problems.append(f"{file} (now {count}, recorded {entry['count']}): {REDIRECT_MESSAGE}")
    return problems


def find_stale(current: dict[str, int], recorded: dict[str, dict]) -> list[str]:
    """Recorded entries whose file now has fewer (or zero) client constructions."""
    stale = []
    for file, entry in sorted(recorded.items()):
        count = current.get(file, 0)
        if count < entry["count"]:
            stale.append(
                f"{file}: recorded {entry['count']}, now {count} — update the entry "
                "(or delete it at zero)"
            )
    return stale


def test_every_test_client_file_is_recorded_at_or_below_its_count() -> None:
    problems = find_new_or_risen(scan_test_clients(REPO_ROOT), _recorded_test_clients(REPO_ROOT))
    assert not problems, "\n".join(problems)


def test_no_stale_test_client_entries() -> None:
    stale = find_stale(scan_test_clients(REPO_ROOT), _recorded_test_clients(REPO_ROOT))
    assert not stale, "\n".join(stale)


def test_new_client_construction_fails_the_ratchet(tmp_path: pathlib.Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "from httpx import AsyncClient\n\n\ndef test_x() -> None:\n    AsyncClient()\n"
    )
    problems = find_new_or_risen(scan_test_clients(tmp_path), recorded={})
    assert len(problems) == 1
    assert "tests/test_example.py (1)" in problems[0]
