"""One helper answers "what time is it now, in UTC".

``app/engine/turn_clock.now_utc`` is that helper. The C-series dedup (#559)
unified it and its reuse-report said plainly: *"do NOT add a third now-helper."*

It came back anyway. Both game modules had grown their own private ``_now()``
returning byte-identically the same thing, four call sites each, and nothing
failed — because the merge left no guard behind. That is the lesson this file
exists to hold: a dedup that ships without a tripwire is a dedup with a
half-life.

The cost of the copies was never divergence — ``datetime.now(timezone.utc)``
is not going to be "hardened" into disagreeing with itself. It is that freezing
the clock, in a test or behind a future clock abstraction, means finding every
copy. Three homes, three patch points, and the two nobody remembers keep
running on the real clock.

So this is a **structural** guard rather than a behavioural one: it reads the
source and fails if a second now-helper appears anywhere in ``app/`` or
``mcp_server/``, under any name. It deliberately matches only the exact shape
``return datetime.now(timezone.utc)`` — a function that merely *uses* the
current time while doing something else (``identity/milestones.build_row``
used to take it as a dict default; the standalone operator connector formats it
into a log timestamp) is not a now-helper and is not caught by that check.

A second guard below closes the gap the first one leaves open: 71 call sites
across 42 files wrote the raw expression ``datetime.now(timezone.utc)`` inline
without ever wrapping it in a function, so the shape-based check above never
saw them. That guard reads the source as plain TEXT and fails on the literal
string ``datetime.now(timezone.utc)`` appearing anywhere in ``app/`` or
``mcp_server/`` outside this module — no AST shape required, so nothing about
*how* it's written (a dict default, a log line, a bare expression) lets it
hide.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The one home. Anything else with this shape is a second copy.
CANONICAL_FILE = "app/engine/turn_clock.py"
CANONICAL_NAME = "now_utc"

# `scripts/agentludum_connector.py` is deliberately a single self-contained file
# that operators install by copying, so it cannot import from `app/` — splitting
# it was adjudicated "deferred" (see failure-archaeology). It is out of scope
# here rather than an exception carved into the rule.
SEARCH_ROOTS = ("app", "mcp_server")

_NOW_BODY = "datetime.now(timezone.utc)"


def _is_now_helper(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the function's whole body is `return datetime.now(timezone.utc)`."""
    body = [
        stmt
        for stmt in fn.body
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    if len(body) != 1:
        return False
    only = body[0]
    if not isinstance(only, ast.Return) or only.value is None:
        return False
    return ast.unparse(only.value) == _NOW_BODY


def _find_now_helpers() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for root in SEARCH_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_now_helper(
                    node
                ):
                    found.append((str(path.relative_to(_REPO_ROOT)), node.name))
    return found


def test_exactly_one_now_helper_exists() -> None:
    helpers = _find_now_helpers()
    extras = [(f, n) for f, n in helpers if f != CANONICAL_FILE]
    assert not extras, (
        f"{len(extras)} second now-helper(s) found: {extras}. "
        f"There is one home for this — {CANONICAL_FILE}:{CANONICAL_NAME} — and it is "
        "importable from anywhere (it depends only on the standard library, so it "
        "cannot create a cycle). Import it instead of writing another."
    )


def test_the_canonical_helper_is_where_this_test_thinks_it_is() -> None:
    """Guard the guard: if now_utc moves or is renamed, fail loudly here rather
    than silently passing because the scan found nothing to compare against."""
    helpers = _find_now_helpers()
    assert (CANONICAL_FILE, CANONICAL_NAME) in helpers, (
        f"expected {CANONICAL_NAME} in {CANONICAL_FILE}; found {helpers}. "
        "If the helper moved, update CANONICAL_FILE/CANONICAL_NAME here."
    )


def test_the_scan_ignores_functions_that_merely_use_the_clock() -> None:
    """A function that reads the time while doing something else is not a copy."""
    uses_clock_but_is_not_a_helper = ast.parse(
        "def f(x=None):\n    return {'at': x or datetime.now(timezone.utc)}\n"
    ).body[0]
    formats_the_clock = ast.parse(
        "def f():\n    return datetime.now(timezone.utc).strftime('%H:%M:%S')\n"
    ).body[0]
    real_helper = ast.parse("def f():\n    return datetime.now(timezone.utc)\n").body[0]
    assert not _is_now_helper(uses_clock_but_is_not_a_helper)
    assert not _is_now_helper(formats_the_clock)
    assert _is_now_helper(real_helper)


def _find_inline_now_calls(root: Path) -> list[tuple[str, int]]:
    """[(file, line)] for every raw-text occurrence of `datetime.now(timezone.utc)`
    outside the one home. Unlike `_find_now_helpers`, this does not require the
    expression to be a function's whole body — it catches the literal text
    wherever it appears (a default, a log line, an inline call), which is what
    let 71 call sites hide from the shape-based check above."""
    found: list[tuple[str, int]] = []
    for top in SEARCH_ROOTS:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(root))
            if rel == CANONICAL_FILE:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _NOW_BODY in line:
                    found.append((rel, lineno))
    return found


def test_no_inline_now_call_outside_the_one_home() -> None:
    hits = _find_inline_now_calls(_REPO_ROOT)
    assert not hits, "\n".join(
        f"{f}:{n}: use now_utc() from {CANONICAL_FILE} instead of writing "
        f"`{_NOW_BODY}` inline" for f, n in hits
    )


def test_the_scan_catches_a_planted_inline_call(tmp_path: Path) -> None:
    """Pins the bug this guard exists to catch: a raw inline call, anywhere in
    app/ or mcp_server/, that never went through a function the shape-based
    check above could see."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from datetime import datetime, timezone\n\n\ndef f():\n    return datetime.now(timezone.utc)\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_now_calls(tmp_path) == [("app/_zz.py", 5)]
