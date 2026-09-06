""""All the players of a match" has one home (PR U5 of the conceptual-duplicate
cleanup): ``app.read_models.matches.load_players``.

Twelve sites used to build ``select(Player).where(Player.match_id == …)`` by
hand instead of calling the loader. Most were routed through it in this PR.

Like ``tests/test_clamp_has_one_home.py`` and
``tests/test_small_idioms_have_one_home.py``, this is a **structural** guard:
it reads the source and fails if the shape reappears anywhere in ``app/`` or
``mcp_server/`` outside the one home, under any variable name or however many
other ``.where()`` clauses are chained alongside the ``match_id`` compare. A
few sites were looked at and deliberately left inline — order-sensitive
callers where routing through the loader's ``ORDER BY seat_name`` would
change behavior. Those are recorded in ``one_home_verdicts.toml``'s
``[[match_players_baseline]]`` table and read from there, not hardcoded here,
so a new site can't quietly hide behind an unaudited exclusion.
"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.find_duplicate_rules import _load_verdicts

_REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = ("app", "mcp_server")

# The one home. Anything else with this shape is a second copy.
CANONICAL_FILE = "app/read_models/matches.py"
CANONICAL_NAME = "load_players"


def _match_players_baseline() -> set[tuple[str, int]]:
    """(file, line) sites recorded in one_home_verdicts.toml — looked at and
    deliberately left inline, not missed."""
    verdicts = _load_verdicts(_REPO_ROOT)
    return {(entry["file"], entry["line"]) for entry in verdicts.get("match_players_baseline", [])}


def _is_select_player_call(node: ast.expr) -> bool:
    """True for `select(Player)`, exactly."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "select"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "Player"
    )


def _is_player_match_id_compare(node: ast.expr) -> bool:
    """True for `Player.match_id == <anything>`, on either side of `==`."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)):
        return False
    sides = (node.left, node.comparators[0])
    return any(
        isinstance(side, ast.Attribute)
        and side.attr == "match_id"
        and isinstance(side.value, ast.Name)
        and side.value.id == "Player"
        for side in sides
    )


def _is_inline_match_players_shape(node: ast.Call) -> bool:
    """True for `select(Player).where(Player.match_id == <expr>, ...)`,
    however many other clauses `.where()` chains alongside the match_id
    compare (an extra seat_name filter, say), and whatever else is chained
    on the outside (`.order_by(...)`, `.scalars()`, ...) — `ast.walk` finds
    this `.where()` call as its own node regardless of what wraps it.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "where"):
        return False
    if not _is_select_player_call(node.func.value):
        return False
    return any(_is_player_match_id_compare(arg) for arg in node.args)


def _find_inline_match_players(root: Path) -> list[tuple[str, int]]:
    """[(file, line)] for every `select(Player).where(Player.match_id == …)`
    shaped call outside the one home."""
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
                if isinstance(node, ast.Call) and _is_inline_match_players_shape(node):
                    found.append((rel, node.lineno))
    return found


def test_no_inline_match_players_query_outside_the_one_home() -> None:
    baseline = _match_players_baseline()
    hits = [h for h in _find_inline_match_players(_REPO_ROOT) if h not in baseline]
    assert not hits, "\n".join(
        f"{f}:{n}: use load_players(db, match_id) from {CANONICAL_FILE} instead of "
        "querying Player by match_id by hand" for f, n in hits
    )


def test_the_canonical_load_players_is_where_this_test_thinks_it_is() -> None:
    """Guard the guard: if load_players moves or is renamed, fail loudly here
    rather than silently passing because the scan found nothing to compare
    against."""
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
    """Pins the bug this file exists to catch: a second `select(Player).where`
    query filtered on match_id, under any variable name, anywhere in app/ or
    mcp_server/."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "async def f(db, match_id):\n"
        "    rows = (await db.execute(select(Player).where(Player.match_id == match_id)))\n"
        "    return rows.scalars().all()\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_match_players(tmp_path) == [("app/_zz.py", 5)]


def test_the_scan_catches_an_extra_chained_filter(tmp_path: Path) -> None:
    """A second `.where()` clause chained alongside `match_id == …` (e.g. an
    extra seat_name filter) is still this shape — see the pre-fix
    app/engine/agent_play.py opponent lookup."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "async def f(db, match_id, seat_name):\n"
        "    return await db.execute(\n"
        "        select(Player).where(Player.match_id == match_id, Player.seat_name == seat_name)\n"
        "    )\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_match_players(tmp_path) == [("app/_zz.py", 6)]


def test_the_scan_catches_an_order_by_chained_on_top(tmp_path: Path) -> None:
    """`.order_by(...)` chained after `.where(...)` doesn't hide the inner
    `.where()` call from the scan — see the pre-fix
    app/games/liars_dice/state.py `_players`."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "async def f(db, match_id):\n"
        "    stmt = select(Player).where(Player.match_id == match_id).order_by(Player.seat_name)\n"
        "    return await db.execute(stmt)\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_match_players(tmp_path) == [("app/_zz.py", 5)]


def test_the_scan_ignores_a_different_filter(tmp_path: Path) -> None:
    """`select(Player).where(...)` on a column other than `match_id` is a
    different query and must not be flagged."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "async def f(db, seat_name):\n"
        "    return await db.execute(select(Player).where(Player.seat_name == seat_name))\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_match_players(tmp_path) == []


def test_the_scan_ignores_an_unrelated_model(tmp_path: Path) -> None:
    """`select(TurnSubmission).where(TurnSubmission.match_id == …)` is a
    different model, not a Player query, and must not be flagged."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "from sqlalchemy import select\n\n\n"
        "async def f(db, match_id):\n"
        "    return await db.execute(\n"
        "        select(TurnSubmission).where(TurnSubmission.match_id == match_id)\n"
        "    )\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_inline_match_players(tmp_path) == []


def test_baseline_sites_are_still_the_inline_shape() -> None:
    """Guard the baseline table itself: every recorded (file, line) must
    still be a live hit of the raw scan (before baseline filtering) — if a
    baselined site's code moved or changed shape, the entry is stale and
    should be removed rather than silently exempting the wrong line."""
    verdicts = _load_verdicts(_REPO_ROOT)
    live = set(_find_inline_match_players(_REPO_ROOT))
    for entry in verdicts.get("match_players_baseline", []):
        site = (entry["file"], entry["line"])
        assert site in live, (
            f"one_home_verdicts.toml match_players_baseline entry {site} is no longer "
            "a live hit of the scan — remove the stale entry"
        )
