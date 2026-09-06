""""Is this player still seated?" has one home (PR U6 of the conceptual-duplicate
cleanup): ``app.engine.seated``.

The rule "a player has not left" used to be written 40 times: 34 as the
SQLAlchemy clause ``Player.left_at.is_(None)`` and 6 as the in-memory check
``player.left_at is None`` — the same rule, spelled two ways, with no
guarantee the two spellings agreed. Both forms now live in
``app/engine/seated.py``: ``seated_filter()`` for a query, ``is_seated(player)``
for a Python object already in hand.

Like ``tests/test_clamp_has_one_home.py`` and
``tests/test_match_players_have_one_home.py``, this is a **structural** guard:
it reads the source and fails if either shape reappears anywhere in ``app/``
or ``mcp_server/`` outside the one home, under any base expression — not just
``Player`` / ``player`` by name, since a future site could alias it. Every
site found at PR U6 time was routed through the new home, so the baseline
table starts empty; a legitimate future exception is recorded in
``one_home_verdicts.toml``'s ``[[seated_baseline]]`` table and read from
there, not hardcoded here, so a new site can't quietly hide behind an
unaudited exclusion.
"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.find_duplicate_rules import _load_verdicts

_REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = ("app", "mcp_server")

# The one home. Anything else with this shape is a second copy.
CANONICAL_FILE = "app/engine/seated.py"
CANONICAL_NAMES = {"seated_filter", "is_seated"}


def _seated_baseline() -> set[tuple[str, int]]:
    """(file, line) sites recorded in one_home_verdicts.toml — looked at and
    deliberately left inline, not missed."""
    verdicts = _load_verdicts(_REPO_ROOT)
    return {(entry["file"], entry["line"]) for entry in verdicts.get("seated_baseline", [])}


def _is_sql_seated_shape(node: ast.expr) -> bool:
    """True for `<anything>.left_at.is_(None)` — the SQLAlchemy clause form,
    however the base expression is named (``Player.left_at``, ``p.left_at``,
    an aliased model, ...)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "is_"):
        return False
    if len(node.args) != 1 or not (isinstance(node.args[0], ast.Constant) and node.args[0].value is None):
        return False
    base = node.func.value
    return isinstance(base, ast.Attribute) and base.attr == "left_at"


def _is_in_memory_seated_shape(node: ast.expr) -> bool:
    """True for `<anything>.left_at is None` — the in-memory form."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Is)):
        return False
    left = node.left
    comparator = node.comparators[0]
    return (
        isinstance(left, ast.Attribute)
        and left.attr == "left_at"
        and isinstance(comparator, ast.Constant)
        and comparator.value is None
    )


def _find_inline_seated_checks(root: Path) -> list[tuple[str, int]]:
    """[(file, line)] for every `left_at.is_(None)` or `left_at is None`
    shaped expression outside the one home."""
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
                if _is_sql_seated_shape(node) or _is_in_memory_seated_shape(node):
                    found.append((rel, node.lineno))
    return found


def test_no_inline_seated_check_outside_the_one_home() -> None:
    baseline = _seated_baseline()
    hits = [h for h in _find_inline_seated_checks(_REPO_ROOT) if h not in baseline]
    assert not hits, "\n".join(
        f"{f}:{n}: use seated_filter() / is_seated() from {CANONICAL_FILE} instead of "
        "checking left_at by hand" for f, n in hits
    )


def test_the_canonical_seated_functions_are_where_this_test_thinks_they_are() -> None:
    """Guard the guard: if seated_filter/is_seated move or are renamed, fail
    loudly here rather than silently passing because the scan found nothing
    to compare against."""
    path = _REPO_ROOT / CANONICAL_FILE
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    missing = CANONICAL_NAMES - names
    assert not missing, (
        f"expected {sorted(CANONICAL_NAMES)} defined in {CANONICAL_FILE}; found {sorted(names)}. "
        "If one moved or was renamed, update CANONICAL_NAMES here."
    )


def test_the_scan_catches_a_planted_sql_copy(tmp_path: Path) -> None:
    """Pins the bug this file exists to catch: a second `left_at.is_(None)`
    clause, on any base expression, anywhere in app/ or mcp_server/."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "def f(match_id):\n"
        "    return select(Player).where(Player.match_id == match_id, Player.left_at.is_(None))\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seated_checks(tmp_path) == [("app/_zz.py", 5)]


def test_the_scan_catches_a_planted_sql_copy_on_an_aliased_base(tmp_path: Path) -> None:
    """The base expression need not be named `Player` — an alias, a joined
    row, anything with a `.left_at` attribute is still this shape."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "def f(match_id):\n"
        "    p = aliased(Player)\n"
        "    return select(p).where(p.left_at.is_(None))\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seated_checks(tmp_path) == [("app/_zz.py", 6)]


def test_the_scan_catches_a_planted_in_memory_copy(tmp_path: Path) -> None:
    """Pins the in-memory form: `<expr>.left_at is None` on an already-loaded
    Player object, under any variable name."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def f(players):\n"
        "    return [p for p in players if p.left_at is None]\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seated_checks(tmp_path) == [("app/_zz.py", 2)]


def test_the_scan_ignores_the_inverse_rule(tmp_path: Path) -> None:
    """`left_at.is_not(None)` ("has left") is a different rule — the inverse
    of "is seated" — and must not be flagged."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "def f():\n"
        "    return select(Player).where(Player.left_at.is_not(None))\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seated_checks(tmp_path) == []


def test_the_scan_ignores_an_unrelated_attribute(tmp_path: Path) -> None:
    """`.is_(None)` / `is None` on some other column is a different check and
    must not be flagged."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "def f():\n"
        "    a = select(Player).where(Player.seat_reserved_until.is_(None))\n"
        "    b = [p for p in [] if p.autopilot_at is None]\n"
        "    return a, b\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_seated_checks(tmp_path) == []


def test_baseline_sites_are_still_the_inline_shape() -> None:
    """Guard the baseline table itself: every recorded (file, line) must
    still be a live hit of the raw scan (before baseline filtering) — if a
    baselined site's code moved or changed shape, the entry is stale and
    should be removed rather than silently exempting the wrong line."""
    verdicts = _load_verdicts(_REPO_ROOT)
    live = set(_find_inline_seated_checks(_REPO_ROOT))
    for entry in verdicts.get("seated_baseline", []):
        site = (entry["file"], entry["line"])
        assert site in live, (
            f"one_home_verdicts.toml seated_baseline entry {site} is no longer "
            "a live hit of the scan — remove the stale entry"
        )
