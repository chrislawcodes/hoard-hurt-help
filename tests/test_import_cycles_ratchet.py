"""Ratchet: a new import cycle among app/ modules must not slip in unnoticed.

Builds a directed graph from every `import app...` / `from app... import ...`
statement in app/ (ast-parsed, both at the top of the file and inside a
function body). Function-body imports count on purpose: two modules that
truly import each other at the top of the file would crash Python on load, so
every real cycle here is closed by at least one deferred import — the
standard workaround for a genuine circular dependency, and exactly the thing
this ratchet exists to catch. Cycles of 2 to 4 modules are recorded in the
[[import_cycles]] table of one_home_verdicts.toml.
"""

from __future__ import annotations

import ast
import pathlib

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_CYCLE_LEN = 4


def _module_and_package(path: pathlib.Path, root: pathlib.Path) -> tuple[str, str]:
    """A file's own dotted module name, and the dotted package a relative
    `from .` import inside it resolves against."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        module = ".".join(parts[:-1])
        package = module
    else:
        module = ".".join(parts)
        package = ".".join(parts[:-1])
    return module, package


def _relative_base(package: str, level: int) -> str:
    parts = package.split(".") if package else []
    climb = level - 1
    if climb:
        parts = parts[: len(parts) - climb] if len(parts) >= climb else []
    return ".".join(parts)


def _resolved_import_targets(tree: ast.Module, package: str, modules: set[str]) -> set[str]:
    """Every real app/ module this file's imports resolve to, at any nesting
    level, resolving a relative `from .` against `package`.

    `from X import Y` names a real module either as X.Y (Y is itself a
    submodule, e.g. `from . import plan_rules`) or as X alone (Y is just an
    attribute defined in X, e.g. `from app.engine.arena import fill_match`) —
    never both, or a sibling-submodule import would wrongly also count as a
    dependency on the package's own __init__.py.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(
                a.name for a in node.names if a.name.startswith("app.") and a.name in modules
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_base(package, node.level)
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            elif node.module and (node.module == "app" or node.module.startswith("app.")):
                base = node.module
            else:
                continue
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                if candidate in modules:
                    targets.add(candidate)
                elif base in modules:
                    targets.add(base)
    return targets


def build_import_graph(root: pathlib.Path) -> dict[str, set[str]]:
    """{module: set of app/ modules it imports}, restricted to modules under app/."""
    app_dir = root / "app"
    modules: dict[str, pathlib.Path] = {}
    for path in sorted(app_dir.rglob("*.py")) if app_dir.is_dir() else []:
        if "__pycache__" in str(path):
            continue
        module, _ = _module_and_package(path, root)
        modules[module] = path

    module_names = set(modules)
    graph: dict[str, set[str]] = {}
    for module, path in modules.items():
        _, package = _module_and_package(path, root)
        tree = ast.parse(path.read_text(), filename=str(path))
        targets = _resolved_import_targets(tree, package, module_names)
        graph[module] = {t for t in targets if t != module}
    return graph


def find_cycles(graph: dict[str, set[str]], max_len: int = MAX_CYCLE_LEN) -> set[frozenset[str]]:
    """Every simple cycle of 2..max_len modules, as the frozenset of its members."""
    cycles: set[frozenset[str]] = set()
    for start in sorted(graph):
        stack: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        while stack:
            node, path = stack.pop()
            for neighbor in graph.get(node, ()):
                if neighbor == start and len(path) >= 2:
                    cycles.add(frozenset(path))
                elif neighbor not in path and len(path) < max_len:
                    stack.append((neighbor, path + (neighbor,)))
    return cycles


def _recorded_cycles(root: pathlib.Path) -> list[frozenset[str]]:
    verdicts = _load_verdicts(root)
    return [frozenset(entry["modules"]) for entry in verdicts.get("import_cycles", [])]


def find_unrecorded(live: set[frozenset[str]], recorded: list[frozenset[str]]) -> list[str]:
    missing = live - set(recorded)
    return [f"new import cycle: {sorted(cycle)}" for cycle in sorted(missing, key=sorted)]


def find_stale(live: set[frozenset[str]], recorded: list[frozenset[str]]) -> list[str]:
    gone = set(recorded) - live
    return [f"recorded cycle no longer exists: {sorted(cycle)}" for cycle in sorted(gone, key=sorted)]


def test_every_import_cycle_is_recorded() -> None:
    live = find_cycles(build_import_graph(REPO_ROOT))
    problems = find_unrecorded(live, _recorded_cycles(REPO_ROOT))
    assert not problems, "\n".join(problems)


def test_no_stale_import_cycle_entries() -> None:
    live = find_cycles(build_import_graph(REPO_ROOT))
    stale = find_stale(live, _recorded_cycles(REPO_ROOT))
    assert not stale, "\n".join(stale)


def test_new_cycle_fails_the_ratchet(tmp_path: pathlib.Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    (app_dir / "a.py").write_text("from app.b import thing\n")
    (app_dir / "b.py").write_text("from app.a import other\n")
    live = find_cycles(build_import_graph(tmp_path))
    problems = find_unrecorded(live, recorded=[])
    assert len(problems) == 1
    assert "app.a" in problems[0] and "app.b" in problems[0]
