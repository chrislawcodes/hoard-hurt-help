"""Ratchet: a file may not grow past its recorded line-count ceiling.

app/ and mcp_server/ files over 400 lines, and tests/ files over 600 lines,
are recorded in the [[oversized]] table of one_home_verdicts.toml. A file that
crosses its limit with no entry, or grows past a recorded one, fails: split
it, or raise the ceiling here with a note saying why. A recorded file that
has shrunk back under the limit is stale.
"""

from __future__ import annotations

import pathlib

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_LIMIT = 400
TEST_LIMIT = 600
# (top-level dir, line limit, recorded "kind")
_BUCKETS = (
    ("app", SOURCE_LIMIT, "source"),
    ("mcp_server", SOURCE_LIMIT, "source"),
    ("tests", TEST_LIMIT, "test"),
)

REDIRECT_MESSAGE = "split it, or raise the ceiling in one_home_verdicts.toml with a note saying why"


def scan_oversized(root: pathlib.Path) -> dict[str, tuple[int, str]]:
    """{relative file path: (line count, kind)} for every file over its bucket's limit."""
    out: dict[str, tuple[int, str]] = {}
    for top, limit, kind in _BUCKETS:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            n = len(path.read_text().splitlines())
            if n > limit:
                out[str(path.relative_to(root))] = (n, kind)
    return out


def _recorded_oversized(root: pathlib.Path) -> dict[str, dict]:
    verdicts = _load_verdicts(root)
    return {entry["file"]: entry for entry in verdicts.get("oversized", [])}


def find_over_ceiling(current: dict[str, tuple[int, str]], recorded: dict[str, dict]) -> list[str]:
    """Files with no recorded entry, or now more lines than their recorded ceiling."""
    problems = []
    for file, (lines, _kind) in sorted(current.items()):
        entry = recorded.get(file)
        if entry is None:
            problems.append(f"{file} ({lines} lines, new): {REDIRECT_MESSAGE}")
        elif lines > entry["lines"]:
            problems.append(f"{file} (now {lines}, recorded {entry['lines']}): {REDIRECT_MESSAGE}")
    return problems


def find_stale(current: dict[str, tuple[int, str]], recorded: dict[str, dict]) -> list[str]:
    """Recorded entries whose file now has fewer lines (or is back under the limit)."""
    stale = []
    for file, entry in sorted(recorded.items()):
        lines = current.get(file, (0, entry["kind"]))[0]
        if lines < entry["lines"]:
            stale.append(f"{file}: recorded {entry['lines']}, now {lines} — update or delete the entry")
    return stale


def test_every_oversized_file_is_recorded_at_or_below_its_ceiling() -> None:
    problems = find_over_ceiling(scan_oversized(REPO_ROOT), _recorded_oversized(REPO_ROOT))
    assert not problems, "\n".join(problems)


def test_no_stale_oversized_entries() -> None:
    stale = find_stale(scan_oversized(REPO_ROOT), _recorded_oversized(REPO_ROOT))
    assert not stale, "\n".join(stale)


def test_growing_past_the_ceiling_fails_the_ratchet(tmp_path: pathlib.Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(500)) + "\n")
    current = scan_oversized(tmp_path)
    recorded = {"app/big.py": {"file": "app/big.py", "lines": 450, "kind": "source"}}
    problems = find_over_ceiling(current, recorded)
    assert len(problems) == 1
    assert "app/big.py (now 500, recorded 450)" in problems[0]
