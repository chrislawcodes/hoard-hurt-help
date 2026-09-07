"""Ratchet: every zero-caller module-level function needs a verdict.

The "delete six dead functions" half of the shape-and-safety cleanup PR found
its targets by hand: grep every candidate name across the repo, keep the ones
with no hit beyond their own `def` line. This test automates that same check
so the next one doesn't have to be found by hand, or missed.

It walks every module-level `def` / `async def` under `app/` and
`mcp_server/` (via `ast`, so a name that only shows up inside a string or a
comment doesn't count as a definition), skips the names that are expected to
have no in-repo caller (dunders, `test_*` functions pytest calls by
discovery, FastAPI route handlers pytest — sorry, uvicorn — calls by URL, and
anything a module re-exports through `__all__`), and then does the same
plain-text scan Part 1 of the cleanup did by hand
(`grep -rnE "\\bname\\b" app mcp_server scripts tests`): does this function's
name appear anywhere in the repo other than on its own definition line?

A function the scan flags needs an entry in this file's
`one_home_verdicts.toml` table, `[[zero_callers]]` — added below the
`[[seated_baseline]]` section. `verdict` is `"unjudged"` (nobody has looked
at it yet) or `"leave"` (looked at, kept on purpose — e.g. a small public
helper meant for callers outside this repo). There is no `"delete"` verdict:
a function someone decides to delete should just be deleted, and its row
removed, not marked and left in place. A flagged function with no entry
fails the build; a recorded entry whose function has since gained a caller
(or been deleted) is stale and also fails — the ratchet only tightens.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = Path(__file__).resolve().parents[1]

# Where a module-level function must be defined to be in scope for this scan.
DEF_ROOTS = ("app", "mcp_server")
# Where its name is looked for, to decide whether anything calls it.
REFERENCE_ROOTS = ("app", "mcp_server", "scripts", "tests")

_WORD_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

REDIRECT_MESSAGE = (
    "delete it, or record why it is kept in one_home_verdicts.toml's [[zero_callers]] table"
)


@dataclass(frozen=True)
class FunctionSite:
    file: str
    name: str
    line: int


def _iter_py_files(root: Path, tops: tuple[str, ...]) -> list[Path]:
    """Every `*.py` file under the given top-level dirs of `root`, sorted,
    skipping `__pycache__`. Dirs that don't exist (a minimal tmp_path tree
    that only builds `app/`) are skipped rather than erroring."""
    files: list[Path] = []
    for top in tops:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            files.append(path)
    return files


def _all_names(tree: ast.Module) -> set[str]:
    """The string literals in a module-level `__all__ = [...]` or `(...)`,
    if the module has one — these are re-exports, not dead code, even when
    nothing in this repo calls them directly."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
            and isinstance(node.value, ast.List | ast.Tuple)
        ):
            return {
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
    return set()


def _decorator_base_name(dec: ast.expr) -> str | None:
    """The leftmost `Name` id of a decorator expression: `router` for both
    `@router.get(...)` and `@router.get("/x", tags=[...])`, `_lobby_router`
    for `@_lobby_router.get(...)`, `None` for a decorator this shape doesn't
    fit (e.g. bare `@dataclass`)."""
    node: ast.expr = dec
    if isinstance(node, ast.Call):
        node = node.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_route_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for a function decorated with `@<x>.<method>(...)` where `<x>`'s
    name contains "router" — this repo's two FastAPI router variables are
    `router` and `_lobby_router` (see app/routes/*.py); matched loosely on
    the substring so a future differently-named router doesn't silently slip
    back out of this exclusion. These are called by uvicorn via URL, not by
    any in-repo caller, so the plain-text reference scan would always flag
    them."""
    return any(
        (base := _decorator_base_name(dec)) is not None and "router" in base.lower()
        for dec in node.decorator_list
    )


def _module_level_function_sites(root: Path) -> list[FunctionSite]:
    """Every module-level def/async def under app/ and mcp_server/ (nested
    defs — methods, closures — are out of scope; only `tree.body` is
    walked), minus dunders, `test_*` functions, FastAPI route handlers, and
    names a module re-exports through `__all__`."""
    sites: list[FunctionSite] = []
    for path in _iter_py_files(root, DEF_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exported = _all_names(tree)
        rel = str(path.relative_to(root))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            name = node.name
            if name.startswith("test_"):
                continue
            if name.startswith("__") and name.endswith("__"):
                continue
            if name in exported:
                continue
            if _is_route_handler(node):
                continue
            sites.append(FunctionSite(rel, name, node.lineno))
    return sites


def _reference_word_counts(root: Path) -> Counter[str]:
    """Every identifier-shaped token in app/, mcp_server/, scripts/, tests/,
    counted across the whole corpus — code, comments, docstrings alike. This
    is a plain text scan, on purpose: the same one Part 1 of the cleanup did
    by hand with `grep -rnE "\\bname\\b"`, so "referenced" means the same
    thing here as it did there."""
    counts: Counter[str] = Counter()
    for path in _iter_py_files(root, REFERENCE_ROOTS):
        counts.update(_WORD_RE.findall(path.read_text(encoding="utf-8")))
    return counts


@lru_cache(maxsize=None)
def find_zero_caller_functions(root: Path) -> tuple[FunctionSite, ...]:
    """The scan this whole file exists to run: module-level functions (per
    `_module_level_function_sites`) whose name appears nowhere in the
    reference corpus except their own definition line."""
    counts = _reference_word_counts(root)
    return tuple(site for site in _module_level_function_sites(root) if counts[site.name] <= 1)


def _zero_caller_baseline() -> dict[tuple[str, str], dict[str, object]]:
    """{(file, name): entry} recorded in one_home_verdicts.toml's
    [[zero_callers]] table — a function the scan still flags, looked at and
    either not yet judged or intentionally kept."""
    verdicts = _load_verdicts(REPO_ROOT)
    return {(entry["file"], entry["name"]): entry for entry in verdicts.get("zero_callers", [])}


def test_every_zero_caller_function_is_recorded() -> None:
    baseline = _zero_caller_baseline()
    unrecorded = [s for s in find_zero_caller_functions(REPO_ROOT) if (s.file, s.name) not in baseline]
    assert not unrecorded, "\n".join(
        f"{s.file}:{s.line}: {s.name} has no caller anywhere in app/, mcp_server/, "
        f"scripts/, or tests/ — {REDIRECT_MESSAGE}"
        for s in unrecorded
    )


def test_no_stale_zero_caller_entries() -> None:
    """Guard the baseline table itself: a recorded entry whose function has
    since gained a caller, or been deleted, is stale and should be removed
    rather than silently exempting a name the live scan no longer flags."""
    baseline = _zero_caller_baseline()
    live = {(s.file, s.name) for s in find_zero_caller_functions(REPO_ROOT)}
    stale = sorted(key for key in baseline if key not in live)
    assert not stale, "\n".join(
        f"{file}:{name}: one_home_verdicts.toml [[zero_callers]] entry is no longer a "
        "live zero-caller hit — remove the stale entry"
        for file, name in stale
    )


def test_the_scan_catches_a_planted_zero_caller_function(tmp_path: Path) -> None:
    """Pins the bug this file exists to catch: a module-level function with
    no reference anywhere else in the tree."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def _never_called() -> None:\n    pass\n")
    assert find_zero_caller_functions(tmp_path) == (FunctionSite("app/_zz.py", "_never_called", 1),)


def test_the_scan_ignores_a_function_with_a_real_caller(tmp_path: Path) -> None:
    """The call is a bare module-level statement, not wrapped in a second
    function — a wrapping function would itself need a caller to avoid being
    flagged, which would test the wrong thing."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def _helper() -> int:\n    return 1\n\n\n_helper()\n")
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_a_caller_in_a_different_file(tmp_path: Path) -> None:
    """The reference scan spans the whole corpus, not just the defining
    file — a caller in tests/ (not just app/ or mcp_server/) still counts."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def _helper() -> int:\n    return 1\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_zz.py").write_text("from app._zz import _helper\n\n\ndef test_it() -> None:\n    assert _helper() == 1\n")
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_test_functions(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def test_something() -> None:\n    pass\n")
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_dunder_functions(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("def __getattr__(name: str) -> None:\n    pass\n")
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_route_handlers(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        'router = object()\n\n\n@router.get("/zz")\nasync def _zz_route() -> None:\n    pass\n'
    )
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_a_router_variable_with_a_prefix(tmp_path: Path) -> None:
    """Not just `router` — this repo's second FastAPI router is named
    `_lobby_router` (see app/routes/web_lobby.py), so the exclusion matches
    on the substring rather than the exact name."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        '_lobby_router = object()\n\n\n@_lobby_router.get("/zz")\nasync def _zz_route() -> None:\n    pass\n'
    )
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_names_reexported_through_all(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        '__all__ = ["_public_but_unreferenced"]\n\n\ndef _public_but_unreferenced() -> None:\n    pass\n'
    )
    assert find_zero_caller_functions(tmp_path) == ()


def test_the_scan_ignores_nested_defs(tmp_path: Path) -> None:
    """Only module-level defs are in scope — a closure defined inside
    another function is not a top-level statement in `tree.body`, so it
    never becomes a candidate in the first place. `_outer` is given its own
    bare-statement caller so this test isolates that one question rather
    than also exercising "is `_outer` itself a zero-caller?"."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def _outer() -> int:\n"
        "    def _inner() -> int:\n"
        "        return 1\n\n"
        "    return _inner()\n\n\n"
        "_outer()\n"
    )
    assert find_zero_caller_functions(tmp_path) == ()
