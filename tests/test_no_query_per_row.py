"""Ratchet: no new database query runs once per row of a loop.

A query inside a `for`/`async for` loop turns a fixed handful of round trips
into one per row — fine for a handful of rows, expensive for a roster or a
season. This file's shape-and-safety PR batched the two real offenders it
found by hand (a per-player agent-version lookup in
``app/read_models/match_export.py``, a per-candidate legacy-id lookup in
``app/routes/web_support.py``) and confirmed a third suspect
(``app/engine/model_verification.py``) really did query per iteration too, so
batched that one as well. This test automates the search so the next one
isn't found by luck.

It walks every ``*.py`` file under ``app/`` with ``ast`` (not a regex, so a
call that only shows up in a string or comment doesn't count) and flags an
``await db.execute(...)`` or ``await db.get(...)`` — matching the session
variable names this repo actually uses, ``db`` and anything ending ``_db``
(see ``app/engine/agent_play_next_turn.py``'s ``check_db``/``after_db``) —
whose nearest enclosing statement, walking outward, is a ``for``/``async
for``. A nested function *defined inside* the loop resets the scan (a call in
a closure isn't the loop's fault); an ``if``/``try``/``with`` inside the loop
body does not (the loop is still the nearest enclosing one). The loop's own
iterable expression counts too, on purpose: a loop that iterates a query's
own already-fetched result *looks* identical, structurally, to one that
re-queries per row, and telling them apart needs a person, not a scanner —
which is exactly why ``app/read_models/engagement_milestones.py`` has a
recorded ``false_positive`` entry below rather than being silently excluded.

A hit needs a ``[[per_row_queries]]`` entry in ``one_home_verdicts.toml``,
keyed by the loop's own line (not the call's — one loop, one entry, however
many per-row calls it holds). A flagged loop with no entry fails the build;
a recorded entry whose loop no longer scans as a hit is stale and also
fails — the ratchet only tightens.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = Path(__file__).resolve().parents[1]

# The session-object method calls that count as a database query.
_SESSION_METHODS = frozenset({"execute", "get"})

REDIRECT_MESSAGE = (
    "batch it into one query outside the loop, or record why it must stay "
    "per-row in one_home_verdicts.toml's [[per_row_queries]] table"
)


@dataclass(frozen=True)
class PerRowQueryHit:
    file: str
    line: int


def _is_db_query_call(node: ast.expr) -> bool:
    """True for `<name>.execute(...)` / `<name>.get(...)` where `<name>` is
    shaped like this repo's async-session variables: literally `db`, or any
    name ending `_db` (`check_db`, `after_db`). Deliberately excludes other
    `.get(...)` callers with unrelated semantics, e.g. the in-memory
    `_cache.get(key, builder)` helpers in app/read_models/*_cache.py."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _SESSION_METHODS:
        return False
    value = func.value
    return isinstance(value, ast.Name) and (value.id == "db" or value.id.endswith("_db"))


class _LoopQueryScanner(ast.NodeVisitor):
    """Walks one module's AST, recording the line of every `for`/`async for`
    whose iterable or body directly contains a per-row-shaped db call.

    `_loop_stack` holds the loops currently in scope. It is pushed for a
    loop's iterable AND body (so either counts), popped before that loop's
    `orelse` (which runs once, not per row), and fully reset — saved, then
    restored — around a nested function/lambda def, so a call inside a
    closure defined in the loop is not blamed on the loop.
    """

    def __init__(self, file: str) -> None:
        self.file = file
        self.hits: set[int] = set()
        self._loop_stack: list[ast.For | ast.AsyncFor] = []

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self._loop_stack.append(node)
        self.visit(node.target)
        self.visit(node.iter)
        for stmt in node.body:
            self.visit(stmt)
        self._loop_stack.pop()
        for stmt in node.orelse:
            self.visit(stmt)

    def _reset_for_nested_scope(self, node: ast.AST) -> None:
        saved, self._loop_stack = self._loop_stack, []
        self.generic_visit(node)
        self._loop_stack = saved

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._reset_for_nested_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._reset_for_nested_scope(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._reset_for_nested_scope(node)

    def visit_Await(self, node: ast.Await) -> None:
        if self._loop_stack and _is_db_query_call(node.value):
            self.hits.add(self._loop_stack[-1].lineno)
        self.generic_visit(node)


def _iter_py_files(root: Path) -> list[Path]:
    """Every `*.py` file under `root`'s `app/`, sorted, skipping __pycache__.
    A minimal tmp_path tree with no `app/` dir yields no files rather than
    erroring."""
    app_dir = root / "app"
    if not app_dir.is_dir():
        return []
    return [p for p in sorted(app_dir.rglob("*.py")) if "__pycache__" not in str(p)]


@lru_cache(maxsize=None)
def find_per_row_queries(root: Path) -> tuple[PerRowQueryHit, ...]:
    """The scan this whole file exists to run: every `for`/`async for` under
    `root/app` whose iterable or body directly holds a per-row-shaped db
    call."""
    hits: list[PerRowQueryHit] = []
    for path in _iter_py_files(root):
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scanner = _LoopQueryScanner(rel)
        scanner.visit(tree)
        hits.extend(PerRowQueryHit(rel, line) for line in sorted(scanner.hits))
    return tuple(hits)


def _per_row_query_baseline() -> dict[tuple[str, int], dict[str, object]]:
    """{(file, line): entry} recorded in one_home_verdicts.toml's
    [[per_row_queries]] table."""
    verdicts = _load_verdicts(REPO_ROOT)
    return {(entry["file"], entry["line"]): entry for entry in verdicts.get("per_row_queries", [])}


def test_every_per_row_query_is_recorded() -> None:
    baseline = _per_row_query_baseline()
    unrecorded = [h for h in find_per_row_queries(REPO_ROOT) if (h.file, h.line) not in baseline]
    assert not unrecorded, "\n".join(
        f"{h.file}:{h.line}: per-row database query — {REDIRECT_MESSAGE}" for h in unrecorded
    )


def test_no_stale_per_row_query_entries() -> None:
    """Guard the baseline table itself: a recorded entry whose loop no longer
    scans as a hit (batched, rewritten, deleted) is stale and should be
    removed rather than silently exempting a line the live scan no longer
    flags."""
    baseline = _per_row_query_baseline()
    live = {(h.file, h.line) for h in find_per_row_queries(REPO_ROOT)}
    stale = sorted(key for key in baseline if key not in live)
    assert not stale, "\n".join(
        f"{file}:{line}: one_home_verdicts.toml [[per_row_queries]] entry is no longer a "
        "live per-row-query hit — remove the stale entry"
        for file, line in stale
    )


def test_the_scan_catches_a_planted_per_row_query(tmp_path: Path) -> None:
    """Pins the bug this file exists to catch: a query awaited directly
    inside a for-loop body."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(db, ids):\n    for row_id in ids:\n        await db.execute(row_id)\n"
    )
    assert find_per_row_queries(tmp_path) == (PerRowQueryHit("app/_zz.py", 2),)


def test_the_scan_catches_db_get_too(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(db, ids):\n    for row_id in ids:\n        await db.get(Model, row_id)\n"
    )
    assert find_per_row_queries(tmp_path) == (PerRowQueryHit("app/_zz.py", 2),)


def test_the_scan_catches_a_call_nested_in_an_if_inside_the_loop(tmp_path: Path) -> None:
    """An `if` inside the loop body does not shield the call underneath it —
    the nearest enclosing LOOP is still the for-statement."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(db, ids):\n"
        "    for row_id in ids:\n"
        "        if row_id:\n"
        "            await db.execute(row_id)\n"
    )
    assert find_per_row_queries(tmp_path) == (PerRowQueryHit("app/_zz.py", 2),)


def test_the_scan_catches_a_query_that_is_only_the_loops_own_iterable(tmp_path: Path) -> None:
    """A loop that iterates a query's own result — `for x in (await
    db.execute(...)).all():` — looks structurally identical to a per-row
    query, so it is flagged too (see engagement_milestones.py's recorded
    false_positive entry)."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(db, q):\n    for row in (await db.execute(q)).all():\n        pass\n"
    )
    assert find_per_row_queries(tmp_path) == (PerRowQueryHit("app/_zz.py", 2),)


def test_the_scan_ignores_a_call_in_a_nested_function_defined_in_the_loop(tmp_path: Path) -> None:
    """A closure defined inside the loop is not blamed on the loop that
    defines it — only a call made directly in the loop's own body/iterable
    counts."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(db, ids):\n"
        "    for row_id in ids:\n"
        "        async def _helper():\n"
        "            await db.execute(row_id)\n"
        "        await _helper()\n"
    )
    assert find_per_row_queries(tmp_path) == ()


def test_the_scan_ignores_a_non_session_object_named_like_a_cache(tmp_path: Path) -> None:
    """`_cache.get(...)` (app/read_models/*_cache.py's in-memory getters) is
    not a database session — the name must be `db` or end `_db`."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(_cache, ids):\n"
        "    for row_id in ids:\n"
        "        await _cache.get(row_id, build)\n"
    )
    assert find_per_row_queries(tmp_path) == ()


def test_the_scan_ignores_a_call_after_the_loop_in_its_orelse(tmp_path: Path) -> None:
    """A for-loop's `else:` clause runs once after the loop completes
    normally, not per row."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "async def f(db, ids):\n"
        "    for row_id in ids:\n"
        "        pass\n"
        "    else:\n"
        "        await db.execute(row_id)\n"
    )
    assert find_per_row_queries(tmp_path) == ()


def test_the_scan_ignores_a_query_outside_any_loop(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text("async def f(db, row_id):\n    await db.execute(row_id)\n")
    assert find_per_row_queries(tmp_path) == ()
