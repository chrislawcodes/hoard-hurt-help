"""Tripwire: every `except Exception` in app/ or mcp_server/ must be explained.

CLAUDE.md's "Fail Loud" rule allows a broad `except Exception` in exactly two
cases:

  (a) the top of a route handler or a background task that must keep running,
      marked with a one-line comment naming which it is — this repo's
      convention is `# background task: <why>` or `# route handler: <why>`.
  (b) a deliberate advisory path that is allowed to fail open, marked
      `# fail-open: advisory only — <why>`.

Anything else is a bug hiding behind a broad catch: it should re-raise, or
narrow to the specific exception type the code actually expects.

This scans every .py file under a root for `except Exception` and flags any
site whose marker is not within one line of the except — the codebase's
existing fail-open comments sit on the line right after the except (the
first line of the block), so both directions are checked.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

_EXCEPT_EXCEPTION = re.compile(r"^\s*except\s+Exception\b")
_MARKER = re.compile(
    r"#.*(fail-open:\s*advisory|background task:|route handler:)", re.IGNORECASE
)


def find_unexplained_except_exception(
    root: pathlib.Path,
) -> list[tuple[pathlib.Path, int]]:
    """(file, 1-based line number) for every `except Exception` under ``root``
    that has no fail-open / background-task / route-handler marker on its own
    line, the line above, or the line below."""
    sites: list[tuple[pathlib.Path, int]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not _EXCEPT_EXCEPTION.match(line):
                continue
            window = lines[max(0, i - 1) : i + 2]
            if not any(_MARKER.search(candidate) for candidate in window):
                sites.append((path, i + 1))
    return sites


def _format(sites: list[tuple[pathlib.Path, int]]) -> str:
    return "\n".join(
        f"{path}:{lineno}: `except Exception` has no fail-open/background-task "
        "marker within one line. Add `# fail-open: advisory only — <why>` if "
        "this is a deliberate best-effort path, `# background task: <why>` or "
        "`# route handler: <why>` if it must keep running, or narrow the "
        "except to the specific exception type the code actually expects."
        for path, lineno in sites
    )


def test_every_except_exception_is_explained() -> None:
    sites: list[tuple[pathlib.Path, int]] = []
    for sub in ("app", "mcp_server"):
        sites.extend(find_unexplained_except_exception(REPO_ROOT / sub))
    assert not sites, _format(sites)


def test_scanner_flags_an_unmarked_except_exception(tmp_path: pathlib.Path) -> None:
    marked = tmp_path / "marked_module.py"
    marked.write_text(
        "def f():\n"
        "    try:\n"
        "        do_thing()\n"
        "    except Exception:\n"
        "        # fail-open: advisory only — this is a fixture, not real code.\n"
        "        pass\n"
    )
    unmarked = tmp_path / "unmarked_module.py"
    unmarked.write_text(
        "def g():\n"
        "    try:\n"
        "        do_other_thing()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    sites = find_unexplained_except_exception(tmp_path)
    assert sites == [(unmarked, 4)]
