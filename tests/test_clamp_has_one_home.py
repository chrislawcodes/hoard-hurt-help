""""Keep a number inside a range" has exactly one home: app/clamp.py.

It used to be written twice, byte-alike: ``app/engine/bots/trust.py`` had
``_clamp_trust`` (``max(-100, min(100, value))``) and
``app/routes/matches_user.py`` had ``_clamp_form_default``
(``max(low, min(high, value))``) — same rule, two names, two places that could
quietly disagree. Both were merged into ``clamp()`` here.

Like ``tests/test_now_helper_single_source.py``, this is a **structural**
guard, not a behavioural one: it reads the source and fails if the
``max(x, min(y, z))`` shape (or the reversed ``min(x, max(y, z))``) appears
anywhere in ``app/`` or ``mcp_server/`` outside the one home. It does not
matter what the inner expressions are, or which argument position they sit
in — that shape IS the rule, under any name.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The one home. Anything else with this shape is a second copy.
CANONICAL_FILE = "app/clamp.py"
CANONICAL_NAME = "clamp"

SEARCH_ROOTS = ("app", "mcp_server")


def _is_clamp_shape(node: ast.Call) -> bool:
    """True for `max(bound, min(bound, value))` or the reversed
    `min(bound, max(bound, value))` — exactly two arguments, exactly one of
    which is a call to the other function. This is the clamp idiom, however
    it is written or named.

    Two arguments that are BOTH calls to the other function — e.g.
    ``min(max(0, a), max(0, b))``, which picks the smaller of two
    independently floored values — is a different rule, not a range clamp
    on one value, and must not be flagged (see app/engine/arena.py's
    ``fill_match_with_bots``, a real false positive this guards against).
    """
    if not isinstance(node.func, ast.Name):
        return False
    if node.func.id == "max":
        inner_name = "min"
    elif node.func.id == "min":
        inner_name = "max"
    else:
        return False
    if len(node.args) != 2:
        return False
    inner_calls = [
        arg
        for arg in node.args
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == inner_name
    ]
    return len(inner_calls) == 1


def _find_inline_clamps(root: Path) -> list[tuple[str, int]]:
    """[(file, line)] for every clamp-shaped call outside the one home."""
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
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _is_clamp_shape(node):
                    found.append((rel, node.lineno))
    return found


def test_no_inline_clamp_outside_the_one_home() -> None:
    hits = _find_inline_clamps(_REPO_ROOT)
    assert not hits, "\n".join(f"{f}:{n}: use clamp() from app/clamp.py" for f, n in hits)


def test_the_canonical_clamp_is_where_this_test_thinks_it_is() -> None:
    """Guard the guard: if clamp moves or is renamed, fail loudly here rather
    than silently passing because the scan found nothing to compare against."""
    path = _REPO_ROOT / CANONICAL_FILE
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert CANONICAL_NAME in names, (
        f"expected {CANONICAL_NAME} defined in {CANONICAL_FILE}; found {sorted(names)}. "
        "If it moved, update CANONICAL_FILE/CANONICAL_NAME here."
    )


def test_the_scan_catches_a_planted_copy(tmp_path: Path) -> None:
    """Pins the bug this file exists to catch: a second clamp, under any name,
    anywhere in app/ or mcp_server/."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def _clamp_something(value, low, high):\n    return max(low, min(high, value))\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_clamps(tmp_path) == [("app/_zz.py", 2)]


def test_the_scan_catches_the_reversed_shape(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def f(value, low, high):\n    return min(high, max(low, value))\n")
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_clamps(tmp_path) == [("app/_zz.py", 2)]


def test_the_scan_ignores_unrelated_max_min_calls(tmp_path: Path) -> None:
    """A plain max() or min() that isn't nesting the other is not a clamp."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def f(a, b):\n    return max(a, b)\n\n\ndef g(items):\n    return min(items)\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_clamps(tmp_path) == []


def test_the_scan_ignores_min_of_two_separately_floored_values(tmp_path: Path) -> None:
    """Pins a real false positive: app/engine/arena.py's `fill_match_with_bots`
    does `min(max(0, a), max(0, b))` — the smaller of two independently
    floored values, not a range clamp on one value — and must not be
    flagged."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def f(a, b):\n    return min(max(0, a), max(0, b))\n")
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_clamps(tmp_path) == []
