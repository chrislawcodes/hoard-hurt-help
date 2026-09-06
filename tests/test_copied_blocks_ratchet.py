"""Ratchet: a NEW copied setup block between two test files must not slip in
unnoticed.

Reimplements the core of the standalone scanner (scripts/scan_shared_windows.py
-- kept outside this repo's tree today, but its algorithm is small enough to
own here): normalized 5-line windows across tests/test_*.py, with the
database-fixture lines excluded exactly like that scanner's ``--skip-db``
flag. Any file pair sharing WINDOW_THRESHOLD or more such windows needs a
``[[copied_blocks]]`` entry in one_home_verdicts.toml recording the count.

A new pair reaching the threshold with no entry fails — the fix is to add a
shared builder to tests/factories.py and migrate both files onto it, not to
record a bigger number here. A recorded pair whose count has since dropped
below the threshold is stale and also fails: the number in the TOML must
stay true today, so the ratchet only ever tightens.
"""

from __future__ import annotations

import collections
import itertools
import pathlib
import re

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

WINDOW = 5
MINLEN = 14
WINDOW_THRESHOLD = 8

# Mirrors scan_shared_windows.py's --skip-db: lines that are part of the
# database engine/session-rebind boilerplate (tests/conftest.py's own
# concern), not scenario setup.
DB_MARKERS = (
    "make_engine(",
    "create_all",
    "_factory(test_engine",
    "app.db.SessionLocal",
    "app.db.engine",
    "test_engine.dispose",
    "yield test_factory",
    "async def reset_db",
    "async def engine",
    "async def session_factory",
    "async def db_session",
)

NEW_PAIR_MESSAGE = "share the setup through tests/factories.py"


def _normalized_lines(path: pathlib.Path) -> list[str]:
    """Collapse whitespace and drop import/blank/comment/decorator/docstring
    lines and anything under MINLEN -- same normalization as
    scan_shared_windows.py."""
    out = []
    for raw in path.read_text().splitlines():
        s = re.sub(r"\s+", " ", raw.strip())
        if len(s) < MINLEN or s.startswith(("import ", "from ", "#", "@", '"""')):
            continue
        out.append(s)
    return out


def pair_window_counts(root: pathlib.Path) -> collections.Counter[tuple[str, str]]:
    """{(file_a, file_b): shared 5-line window count} across root/tests/test_*.py.

    file_a/file_b are paths relative to `root` ("tests/test_x.py"),
    alphabetically ordered. Mirrors scan_shared_windows.py --skip-db exactly
    (same window size, minimum line length, and excluded database markers) so
    the numbers recorded in one_home_verdicts.toml stay comparable to that
    script's own output.
    """
    tests_dir = root / "tests"
    windows: dict[tuple[str, ...], set[str]] = collections.defaultdict(set)
    if tests_dir.is_dir():
        for path in sorted(tests_dir.glob("test_*.py")):
            norm = _normalized_lines(path)
            rel = str(path.relative_to(root))
            for i in range(len(norm) - WINDOW + 1):
                w = tuple(norm[i : i + WINDOW])
                if any(marker in line for line in w for marker in DB_MARKERS):
                    continue
                windows[w].add(rel)

    pairs: collections.Counter[tuple[str, str]] = collections.Counter()
    for files in windows.values():
        if len(files) < 2:
            continue
        for x, y in itertools.combinations(sorted(files), 2):
            pairs[(x, y)] += 1
    return pairs


def _recorded_copied_blocks(root: pathlib.Path) -> dict[tuple[str, str], int]:
    verdicts = _load_verdicts(root)
    return {
        (entry["file_a"], entry["file_b"]): entry["windows"]
        for entry in verdicts.get("copied_blocks", [])
    }


def find_new_violations(
    counts: collections.Counter[tuple[str, str]], recorded: dict[tuple[str, str], int]
) -> list[str]:
    """Pairs at or above the threshold with no recorded entry."""
    problems = []
    for pair, n in sorted(counts.items()):
        if n >= WINDOW_THRESHOLD and pair not in recorded:
            a, b = pair
            problems.append(f"{a} <-> {b}: {n} shared windows, unrecorded. {NEW_PAIR_MESSAGE}")
    return problems


def find_stale_entries(
    counts: collections.Counter[tuple[str, str]], recorded: dict[tuple[str, str], int]
) -> list[str]:
    """Recorded entries whose pair has dropped below the threshold. The
    ratchet only tightens, so a lower count has to be re-recorded (or the
    entry removed), never left standing at a number that's no longer true."""
    stale = []
    for (a, b), recorded_n in sorted(recorded.items()):
        current_n = counts.get((a, b), 0)
        if current_n < WINDOW_THRESHOLD:
            stale.append(
                f"{a} <-> {b}: recorded at {recorded_n} windows, now {current_n} "
                "(below the threshold) -- update or remove this one_home_verdicts.toml entry"
            )
    return stale


def test_every_copied_block_at_or_above_threshold_is_recorded() -> None:
    problems = find_new_violations(
        pair_window_counts(REPO_ROOT), _recorded_copied_blocks(REPO_ROOT)
    )
    assert not problems, "\n".join(problems)


def test_no_stale_copied_block_entries() -> None:
    stale = find_stale_entries(pair_window_counts(REPO_ROOT), _recorded_copied_blocks(REPO_ROOT))
    assert not stale, "\n".join(stale)


def test_new_over_threshold_pair_is_reported(tmp_path: pathlib.Path) -> None:
    """A synthetic pair of files sharing >= WINDOW_THRESHOLD windows, with no
    recorded entry, is reported as a new violation."""
    (tmp_path / "tests").mkdir()
    # A sliding window of size WINDOW over N lines yields N - WINDOW + 1
    # windows, so WINDOW_THRESHOLD + WINDOW - 1 identical lines produce
    # exactly WINDOW_THRESHOLD shared windows once both files hold them.
    shared_lines = [
        f"    scenario_setup_call_number_{i}(some_argument)"
        for i in range(WINDOW_THRESHOLD + WINDOW - 1)
    ]
    body = "\n".join(shared_lines) + "\n"
    (tmp_path / "tests" / "test_a.py").write_text(body)
    (tmp_path / "tests" / "test_b.py").write_text(body)

    problems = find_new_violations(pair_window_counts(tmp_path), recorded={})

    assert len(problems) == 1
    assert "tests/test_a.py <-> tests/test_b.py" in problems[0]
    assert NEW_PAIR_MESSAGE in problems[0]
