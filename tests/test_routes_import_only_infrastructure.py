"""The routes-import-only-routers rule: app/routes/ modules import route
infrastructure from each other, not act as a general library.

A module under app/routes/ may import a *specific name* out of another
app/routes/ module only if that other module is recorded in the
`[[route_infrastructure]]` table of one_home_verdicts.toml. Anything else that
looks like a pure query or presenter belongs in app/read_models/ instead —
three modules moved there in this PR: agents_queries.py -> read_models/
agents_owned.py, connections_queries.py -> read_models/connections_owned.py,
agents_health_presenter.py -> read_models/agents_health.py.

Not scanned at all: a parent module importing a sibling *page* module by name
to mount its `router` — e.g. `from app.routes import agents_create,
agents_detail, agents_list` in agents_setup.py, used only as
`agents_create.router.routes`. That is router composition, the ordinary way a
FastAPI app nests routers, not one route module reaching into another's
internals. It is syntactically distinct from the thing this test restricts:
it never dots into a specific submodule (`from app.routes.X import name` or
`import app.routes.X`), it only lists submodules as plain import names
(`from app.routes import X`) and uses them solely via `X.router`.
"""

from __future__ import annotations

import ast
import pathlib

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

REDIRECT_MESSAGE = (
    "move it to app/read_models/, or record it as route infrastructure in "
    "one_home_verdicts.toml"
)


def _relative_base(package: str, level: int) -> str:
    parts = package.split(".") if package else []
    climb = level - 1
    if climb:
        parts = parts[: len(parts) - climb] if len(parts) >= climb else []
    return ".".join(parts)


def _route_modules(routes_dir: pathlib.Path) -> dict[str, pathlib.Path]:
    """{dotted module name -> file path} for every app/routes/*.py file."""
    modules: dict[str, pathlib.Path] = {}
    for path in sorted(routes_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        modules[f"app.routes.{path.stem}"] = path
    return modules


def find_cross_route_imports(root: pathlib.Path) -> list[tuple[str, int, str]]:
    """[(importing file, lineno, imported dotted module)] for every import of
    another app/routes/ module *by its own dotted path* — the "borrow a
    specific name" shape (`from app.routes.X import name`, `import
    app.routes.X`, and their relative equivalents). Excludes the bare
    `from app.routes import X` / `from . import X` router-mounting form,
    which never dots into a specific submodule.
    """
    routes_dir = root / "app" / "routes"
    if not routes_dir.is_dir():
        return []
    modules = _route_modules(routes_dir)
    package = "app.routes"

    hits: list[tuple[str, int, str]] = []
    for own_module, path in sorted(modules.items()):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = str(path.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules and alias.name != own_module:
                        hits.append((rel_path, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = _relative_base(package, node.level)
                    if node.module:
                        base = f"{base}.{node.module}" if base else node.module
                elif node.module:
                    base = node.module
                else:
                    continue
                if base == package:
                    # `from app.routes import X` (or `from . import X`): X is
                    # named as a plain import name, not dotted into — the
                    # router-mounting idiom. Not scanned.
                    continue
                if base in modules and base != own_module:
                    hits.append((rel_path, node.lineno, base))
    return hits


def _dotted_to_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


def _recorded_infrastructure(root: pathlib.Path) -> set[str]:
    verdicts = _load_verdicts(root)
    return {entry["module"] for entry in verdicts.get("route_infrastructure", [])}


def find_undeclared(hits: list[tuple[str, int, str]], recorded: set[str]) -> list[str]:
    problems = []
    for importing_file, lineno, imported_module in hits:
        imported_path = _dotted_to_path(imported_module)
        if imported_path not in recorded:
            problems.append(
                f"{importing_file}:{lineno} imports {imported_module} "
                f"({imported_path}): {REDIRECT_MESSAGE}"
            )
    return problems


def test_every_cross_route_import_is_declared_infrastructure() -> None:
    hits = find_cross_route_imports(REPO_ROOT)
    recorded = _recorded_infrastructure(REPO_ROOT)
    problems = find_undeclared(hits, recorded)
    assert not problems, "\n".join(problems)


def test_undeclared_cross_route_import_fails_the_ratchet(tmp_path: pathlib.Path) -> None:
    routes_dir = tmp_path / "app" / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "__init__.py").write_text("")
    (routes_dir / "web_support.py").write_text("def safe_internal_next():\n    pass\n")
    (routes_dir / "agents_list.py").write_text(
        "from app.routes.web_support import safe_internal_next\n"
    )
    hits = find_cross_route_imports(tmp_path)
    problems = find_undeclared(hits, recorded=set())
    assert len(problems) == 1
    assert "app/routes/agents_list.py" in problems[0]
    assert "app.routes.web_support" in problems[0]
    assert "app/routes/web_support.py" in problems[0]
