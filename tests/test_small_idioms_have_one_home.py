"""Three small idioms, one home each (PR U3 of the conceptual-duplicate cleanup).

Each is a shared shape that was being hand-rolled at more than one call site:

- "seconds left until a deadline" -> ``app.engine.turn_clock.seconds_until``
- "make a naive timestamp aware" -> ``app.aware_datetime.ensure_aware``
- "humanize a hyphenated slug" -> ``app.match_naming.humanize_game_type``

Like ``tests/test_clamp_has_one_home.py``, these are **structural** guards:
each reads the source and fails if the hand-rolled shape reappears anywhere
in ``app/`` or ``mcp_server/`` outside the one home, under any name or
variable. A handful of sites were looked at and deliberately left as-is
(different rule, or a real behaviour difference) — those are recorded in
``one_home_verdicts.toml``'s ``[[idiom_baselines]]`` table and read from
there, not hardcoded here, so a new site can't quietly hide behind an
unaudited exclusion.
"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.find_duplicate_rules import _load_verdicts

_REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = ("app", "mcp_server")


def _idiom_baselines(idiom: str) -> set[tuple[str, int]]:
    """(file, line) sites recorded under this idiom in one_home_verdicts.toml —
    looked at and deliberately left, not missed."""
    verdicts = _load_verdicts(_REPO_ROOT)
    return {
        (entry["file"], entry["line"])
        for entry in verdicts.get("idiom_baselines", [])
        if entry["idiom"] == idiom
    }


def _iter_calls(root: Path, canonical_file: str):
    """Yield (relative_path, ast.Call) for every Call node in every .py file
    under SEARCH_ROOTS, skipping the one home."""
    for top in SEARCH_ROOTS:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(root))
            if rel == canonical_file:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    yield rel, node


# --- idiom (a): seconds left until a deadline -------------------------------
# app/engine/turn_clock.py::seconds_until

SECONDS_UNTIL_FILE = "app/engine/turn_clock.py"
SECONDS_UNTIL_NAME = "seconds_until"


def _is_now_utc_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "now_utc"


def _is_seconds_until_shape(node: ast.Call) -> bool:
    """True for `(<expr> - now_utc()).total_seconds()`, however the deadline
    expression on the left is written — the defining feature of this idiom is
    subtracting a fresh `now_utc()` call and calling `.total_seconds()` on the
    result, which is exactly what `seconds_until` does instead."""
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "total_seconds"):
        return False
    obj = node.func.value
    if not (isinstance(obj, ast.BinOp) and isinstance(obj.op, ast.Sub)):
        return False
    return _is_now_utc_call(obj.left) or _is_now_utc_call(obj.right)


def _find_inline_seconds_until(root: Path) -> list[tuple[str, int]]:
    return [
        (rel, node.lineno)
        for rel, node in _iter_calls(root, SECONDS_UNTIL_FILE)
        if _is_seconds_until_shape(node)
    ]


def test_no_inline_seconds_until_outside_the_one_home() -> None:
    baseline = _idiom_baselines(SECONDS_UNTIL_NAME)
    hits = [h for h in _find_inline_seconds_until(_REPO_ROOT) if h not in baseline]
    assert not hits, "\n".join(
        f"{f}:{n}: use seconds_until(deadline) from {SECONDS_UNTIL_FILE} instead of "
        "subtracting now_utc() by hand" for f, n in hits
    )


def test_the_canonical_seconds_until_is_where_this_test_thinks_it_is() -> None:
    """Guard the guard: if seconds_until moves or is renamed, fail loudly here
    rather than silently passing because the scan found nothing to compare
    against."""
    tree = ast.parse((_REPO_ROOT / SECONDS_UNTIL_FILE).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert SECONDS_UNTIL_NAME in names, (
        f"expected {SECONDS_UNTIL_NAME} defined in {SECONDS_UNTIL_FILE}; found {sorted(names)}. "
        "If it moved, update SECONDS_UNTIL_FILE/SECONDS_UNTIL_NAME here."
    )


def test_the_seconds_until_scan_catches_a_planted_copy(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from app.engine.turn_clock import now_utc\n\n\n"
        "def f(deadline):\n    return (deadline - now_utc()).total_seconds()\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seconds_until(tmp_path) == [("app/_zz.py", 5)]


def test_the_seconds_until_scan_ignores_a_plain_total_seconds_call(tmp_path: Path) -> None:
    """Subtracting two other things and calling .total_seconds() is a
    different (and common) idiom in this repo — only a fresh now_utc() call
    on one side of the subtraction is this specific one."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def f(a, b):\n    return (a - b).total_seconds()\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seconds_until(tmp_path) == []


# --- idiom (c): make a naive timestamp aware ---------------------------------
# app/aware_datetime.py::ensure_aware

ENSURE_AWARE_FILE = "app/aware_datetime.py"
ENSURE_AWARE_NAME = "ensure_aware"


def _is_tzinfo_replace_shape(node: ast.Call) -> bool:
    """True for `<expr>.replace(tzinfo=timezone.utc)`, under any name for the
    expression being made aware."""
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "replace"):
        return False
    for kw in node.keywords:
        if kw.arg != "tzinfo":
            continue
        v = kw.value
        if (
            isinstance(v, ast.Attribute)
            and v.attr == "utc"
            and isinstance(v.value, ast.Name)
            and v.value.id == "timezone"
        ):
            return True
    return False


def _find_inline_tzinfo_replace(root: Path) -> list[tuple[str, int]]:
    return [
        (rel, node.lineno)
        for rel, node in _iter_calls(root, ENSURE_AWARE_FILE)
        if _is_tzinfo_replace_shape(node)
    ]


def test_no_inline_tzinfo_replace_outside_the_one_home() -> None:
    baseline = _idiom_baselines(ENSURE_AWARE_NAME)
    hits = [h for h in _find_inline_tzinfo_replace(_REPO_ROOT) if h not in baseline]
    assert not hits, "\n".join(
        f"{f}:{n}: use ensure_aware(...) from {ENSURE_AWARE_FILE} instead of "
        "replace(tzinfo=timezone.utc) by hand" for f, n in hits
    )


def test_the_canonical_ensure_aware_is_where_this_test_thinks_it_is() -> None:
    tree = ast.parse((_REPO_ROOT / ENSURE_AWARE_FILE).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert ENSURE_AWARE_NAME in names, (
        f"expected {ENSURE_AWARE_NAME} defined in {ENSURE_AWARE_FILE}; found {sorted(names)}. "
        "If it moved, update ENSURE_AWARE_FILE/ENSURE_AWARE_NAME here."
    )


def test_the_tzinfo_replace_scan_catches_a_planted_copy(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from datetime import timezone\n\n\n"
        "def f(dt):\n    if dt.tzinfo is None:\n        dt = dt.replace(tzinfo=timezone.utc)\n    return dt\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_tzinfo_replace(tmp_path) == [("app/_zz.py", 6)]


def test_the_tzinfo_replace_scan_ignores_other_replace_calls(tmp_path: Path) -> None:
    """A `.replace(...)` that isn't setting tzinfo to timezone.utc — e.g. a
    string replace, or a datetime replace on a different field — is not this
    idiom."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def f(name, dt):\n"
        "    name = name.replace('-', ' ')\n"
        "    return dt.replace(hour=0)\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_tzinfo_replace(tmp_path) == []


# --- idiom (d): humanize a hyphenated slug -----------------------------------
# app/match_naming.py::humanize_game_type

HUMANIZE_FILE = "app/match_naming.py"
HUMANIZE_NAME = "humanize_game_type"


def _is_slug_humanize_shape(node: ast.Call) -> bool:
    """True for `<expr>.replace("-", " ").title()`, under any name for the
    slug being humanized."""
    if node.args or not (isinstance(node.func, ast.Attribute) and node.func.attr == "title"):
        return False
    obj = node.func.value
    if not (isinstance(obj, ast.Call) and isinstance(obj.func, ast.Attribute) and obj.func.attr == "replace"):
        return False
    if len(obj.args) != 2:
        return False
    first, second = obj.args
    return (
        isinstance(first, ast.Constant) and first.value == "-"
        and isinstance(second, ast.Constant) and second.value == " "
    )


def _find_inline_slug_humanize(root: Path) -> list[tuple[str, int]]:
    return [
        (rel, node.lineno)
        for rel, node in _iter_calls(root, HUMANIZE_FILE)
        if _is_slug_humanize_shape(node)
    ]


def test_no_inline_slug_humanize_outside_the_one_home() -> None:
    baseline = _idiom_baselines(HUMANIZE_NAME)
    hits = [h for h in _find_inline_slug_humanize(_REPO_ROOT) if h not in baseline]
    assert not hits, "\n".join(
        f"{f}:{n}: use humanize_game_type(slug) from {HUMANIZE_FILE} instead of "
        're.replace("-", " ").title() by hand' for f, n in hits
    )


def test_the_canonical_humanize_is_where_this_test_thinks_it_is() -> None:
    tree = ast.parse((_REPO_ROOT / HUMANIZE_FILE).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert HUMANIZE_NAME in names, (
        f"expected {HUMANIZE_NAME} defined in {HUMANIZE_FILE}; found {sorted(names)}. "
        "If it moved, update HUMANIZE_FILE/HUMANIZE_NAME here."
    )


def test_the_slug_humanize_scan_catches_a_planted_copy(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        'def f(slug):\n    return slug.replace("-", " ").title()\n'
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_slug_humanize(tmp_path) == [("app/_zz.py", 2)]


def test_the_slug_humanize_scan_ignores_a_different_separator() -> None:
    """roster.py's personality_display_name humanizes on an underscore, not a
    hyphen — a different rule, not a copy of this one, and must not be
    flagged."""
    expr = ast.parse('x.replace("_", " ").title()').body[0]
    assert isinstance(expr, ast.Expr) and isinstance(expr.value, ast.Call)
    assert not _is_slug_humanize_shape(expr.value)


def test_baselined_sites_are_recorded_with_the_right_idiom() -> None:
    """Guard the baseline table itself: every entry names one of the three
    idioms this file actually scans for, so a typo'd `idiom` value can't
    silently stop filtering a site."""
    verdicts = _load_verdicts(_REPO_ROOT)
    known = {SECONDS_UNTIL_NAME, ENSURE_AWARE_NAME, HUMANIZE_NAME}
    for entry in verdicts.get("idiom_baselines", []):
        assert entry["idiom"] in known, (
            f"unrecognized idiom {entry['idiom']!r} in one_home_verdicts.toml "
            f"idiom_baselines; expected one of {sorted(known)}"
        )
