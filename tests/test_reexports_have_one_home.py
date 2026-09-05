"""The reexports ratchet: every import-only name in `__all__` needs a verdict.

A module whose `__all__` re-exports a name it only imports — never defines —
gives that name two import paths: the module that defines it, and the module
that re-exports it. PR C of the "One Home, Kept" plan removed the last big
batch of these (app/engine/connection_health.py and three route aggregators).
These tests keep a new one from shipping unrecorded, the same way
test_one_home_ratchet.py does for duplicate function names.
"""

from __future__ import annotations

import ast
import pathlib

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("app", "mcp_server")


def _all_names(tree: ast.Module) -> list[str] | None:
    """The string literals in a module-level `__all__ = [...]`, if it has one."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            return [
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    return None


def _module_level_bindings(tree: ast.Module) -> dict[str, str]:
    """Every top-level name this module binds, to how it is bound: "def" (a
    function or class), "assign", or "import"."""
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings[node.name] = "def"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = "assign"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bindings[node.target.id] = "assign"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bindings[alias.asname or alias.name] = "import"
    return bindings


def _import_only_reexports(path: pathlib.Path) -> list[str]:
    """`__all__` names this module binds only via an import, never a def/class/assignment."""
    tree = ast.parse(path.read_text(), filename=str(path))
    all_names = _all_names(tree)
    if not all_names:
        return []
    bindings = _module_level_bindings(tree)
    return sorted(name for name in all_names if bindings.get(name) == "import")


def _live_reexports() -> dict[str, list[str]]:
    live: dict[str, list[str]] = {}
    for top in SOURCE_ROOTS:
        for path in sorted((REPO_ROOT / top).rglob("*.py")):
            names = _import_only_reexports(path)
            if names:
                live[str(path.relative_to(REPO_ROOT))] = names
    return live


def _recorded_reexports() -> dict[str, dict]:
    verdicts = _load_verdicts(REPO_ROOT)
    return {entry["module"]: entry for entry in verdicts.get("reexports", [])}


def test_every_import_only_reexport_has_a_verdict() -> None:
    """A name newly re-exported via `__all__` must get a recorded verdict before
    it can ship quietly — this is the ratchet."""
    live = _live_reexports()
    recorded = _recorded_reexports()

    undeclared = []
    for module, names in live.items():
        entry = recorded.get(module)
        known = set(entry["names"]) if entry else set()
        missing = sorted(set(names) - known)
        if missing:
            undeclared.append(f"{module}: {missing}")

    assert not undeclared, (
        "New name(s) re-exported via __all__ with no recorded verdict — import "
        "it from its real module. If a re-export is genuinely wanted, add a "
        "[[reexports]] entry to one_home_verdicts.toml recording that on purpose:\n"
        + "\n".join(undeclared)
    )


def test_no_stale_reexport_entries() -> None:
    """A verdict is a claim about the code at the time it was written. If the
    code moved on, the entry has to move with it."""
    live = _live_reexports()
    recorded = _recorded_reexports()

    stale = []
    for module, entry in recorded.items():
        current = set(live.get(module, []))
        gone = sorted(set(entry["names"]) - current)
        if gone:
            stale.append(f"{module}: {gone}")

    assert not stale, (
        "one_home_verdicts.toml [[reexports]] entries no longer match the code — "
        "the name(s) below are no longer import-only re-exports there (moved, "
        "removed, or now defined locally). Update or delete the entry:\n"
        + "\n".join(stale)
    )
