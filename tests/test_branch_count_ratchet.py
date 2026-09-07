"""Ratchet: a function's branch count may not grow past its recorded ceiling.

A branch, for this scan, is one of: `if`, `for`, `while`, an `except`
handler, each `with` item, boolean `and`/`or`, a conditional expression
(`x if c else y`), and each comprehension generator/condition. Every
function under app/ or mcp_server/ over 20 branches must have an entry in
one_home_verdicts.toml's `[[oversized_functions]]` table, recording its
branch count as a ceiling. A function with no entry that crosses 20
branches, or a recorded function that grows past its recorded ceiling,
fails: split it, or raise the ceiling with a note saying why. A recorded
entry whose function has shrunk back under its ceiling (or under 20
branches entirely) is stale.

A function's own count does not include a nested `def`'s branches — a
nested function is scored separately, as its own entry, so its branches
are never double-counted against the function that contains it. A
`lambda`, which never gets its own entry (it has no name to report), is
different: its branches still count toward whichever named function
contains it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("app", "mcp_server")
BRANCH_LIMIT = 20

REDIRECT_MESSAGE = "split it, or record it in one_home_verdicts.toml's [[oversized_functions]] table"

_BRANCH_STMT_TYPES = (ast.If, ast.For, ast.AsyncFor, ast.While)


@dataclass(frozen=True)
class FunctionSite:
    file: str
    name: str
    line: int
    branches: int


def _count_own_branches(node: ast.AST) -> int:
    """Branches belonging to `node` itself, not to any nested def/class."""
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # scored separately, as its own FunctionSite
        if isinstance(child, _BRANCH_STMT_TYPES):
            count += 1
        elif isinstance(child, ast.ExceptHandler):
            count += 1
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            count += len(child.items)
        elif isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        elif isinstance(child, ast.IfExp):
            count += 1
        elif isinstance(child, ast.comprehension):
            count += 1 + len(child.ifs)
        count += _count_own_branches(child)
    return count


def _iter_py_files(root: Path, tops: tuple[str, ...]) -> list[Path]:
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


def scan_branch_counts(root: Path) -> tuple[FunctionSite, ...]:
    """Every def/async def under app/ and mcp_server/ — module-level,
    methods, and nested closures alike, each as its own entry — with its
    own branch count (see module docstring for what counts as a branch and
    how nesting is handled)."""
    sites: list[FunctionSite] = []
    for path in _iter_py_files(root, SCAN_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = str(path.relative_to(root))

        def visit(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qname = f"{prefix}{child.name}"
                    sites.append(
                        FunctionSite(rel, qname, child.lineno, _count_own_branches(child))
                    )
                    visit(child, f"{qname}.")
                elif isinstance(child, ast.ClassDef):
                    visit(child, f"{prefix}{child.name}.")
                else:
                    visit(child, prefix)

        visit(tree, "")
    return tuple(sites)


def find_over_branch_limit(root: Path) -> tuple[FunctionSite, ...]:
    return tuple(s for s in scan_branch_counts(root) if s.branches > BRANCH_LIMIT)


def _recorded_oversized_functions() -> dict[tuple[str, str], dict]:
    """{(file, name): entry} recorded in one_home_verdicts.toml's
    [[oversized_functions]] table."""
    verdicts = _load_verdicts(REPO_ROOT)
    return {(entry["file"], entry["name"]): entry for entry in verdicts.get("oversized_functions", [])}


def test_every_function_over_the_branch_limit_is_recorded_at_or_below_its_ceiling() -> None:
    recorded = _recorded_oversized_functions()
    problems = []
    for site in find_over_branch_limit(REPO_ROOT):
        entry = recorded.get((site.file, site.name))
        if entry is None:
            problems.append(
                f"{site.file}:{site.line}: {site.name} has {site.branches} branches (new): "
                f"{REDIRECT_MESSAGE}"
            )
        elif site.branches > entry["branches"]:
            problems.append(
                f"{site.file}:{site.line}: {site.name} now has {site.branches} branches, "
                f"recorded ceiling {entry['branches']}: {REDIRECT_MESSAGE}"
            )
    assert not problems, "\n".join(problems)


def test_no_stale_oversized_function_entries() -> None:
    """Guard the table itself: a recorded entry whose function has shrunk
    back under its ceiling (or under the limit entirely) is stale."""
    recorded = _recorded_oversized_functions()
    live = {(s.file, s.name): s.branches for s in find_over_branch_limit(REPO_ROOT)}
    stale = []
    for (file, name), entry in sorted(recorded.items()):
        branches = live.get((file, name))
        if branches is None:
            stale.append(
                f"{file}:{name}: one_home_verdicts.toml [[oversized_functions]] entry is no "
                "longer a live hit — remove the stale entry"
            )
        elif branches < entry["branches"]:
            stale.append(
                f"{file}:{name}: recorded ceiling {entry['branches']}, now {branches} — "
                "lower the ceiling or drop the entry"
            )
    assert not stale, "\n".join(stale)


def test_the_scan_catches_a_planted_function_over_the_branch_limit(tmp_path: Path) -> None:
    """Pins the bug this file exists to catch: a function with more than
    BRANCH_LIMIT branches and no recorded entry."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    body = "\n".join(f"    if x == {i}:\n        x += 1" for i in range(BRANCH_LIMIT + 5))
    (app_dir / "_zz.py").write_text(f"def _many_branches(x: int) -> int:\n{body}\n    return x\n")

    hits = find_over_branch_limit(tmp_path)

    assert len(hits) == 1
    assert hits[0].file == "app/_zz.py"
    assert hits[0].name == "_many_branches"
    assert hits[0].branches == BRANCH_LIMIT + 5


def test_the_scan_does_not_double_count_a_nested_function_into_its_parent(tmp_path: Path) -> None:
    """A nested `def`'s branches are its own — they must not also inflate
    the branch count of the function that contains it."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "def _outer(x: int) -> int:\n"
        "    def _inner(y: int) -> int:\n"
        "        if y == 1:\n"
        "            return 1\n"
        "        return 0\n\n"
        "    if x == 1:\n"
        "        return _inner(x)\n"
        "    return 0\n"
    )
    sites = {s.name: s.branches for s in scan_branch_counts(tmp_path)}
    assert sites["_outer"] == 1
    assert sites["_outer._inner"] == 1
