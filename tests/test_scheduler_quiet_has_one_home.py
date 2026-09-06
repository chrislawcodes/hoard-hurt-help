"""Ratchet: a NEW hand-rolled scheduler patch must not slip in unnoticed.

tests/conftest.py's `quiet_scheduler` fixture is the one home for silencing
`app.engine.scheduler`'s background turn loop: it rebinds `publish`,
`SessionLocal`, `_wait_for_messages`, and `_wait_for_turn` on the module. A
tests/test_*.py file that still does `monkeypatch.setattr(scheduler, "<one of
those four>", ...)` directly is checked against the `[[scheduler_patches]]`
table in one_home_verdicts.toml, the same ratchet shape as
tests/test_fixtures_have_one_home.py.

Scanning is AST-based (not a text/line scan) specifically so a call wrapped
across several lines is still caught: `ast.parse` reports one node for the
whole `monkeypatch.setattr(...)` call regardless of how its arguments are
laid out, with `node.lineno` pointing at the line the call starts on.
"""

from __future__ import annotations

import ast
import pathlib

from scripts.find_duplicate_rules import _load_verdicts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

PATCHED_ATTRS = frozenset({"publish", "SessionLocal", "_wait_for_messages", "_wait_for_turn"})
REDIRECT_MESSAGE = "use the quiet_scheduler fixture"


def _is_setattr_call(node: ast.expr) -> bool:
    """True for `<anything>.setattr(...)` — in practice always `monkeypatch.setattr(...)`."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr"


def _is_bare_scheduler_name(node: ast.expr) -> bool:
    """True for the bare `scheduler` module reference — not `scheduler.registry`
    or `scheduler.logger`, which are different objects the ratchet doesn't cover."""
    return isinstance(node, ast.Name) and node.id == "scheduler"


def scan_scheduler_patches(root: pathlib.Path) -> dict[tuple[str, str], list[int]]:
    """{(relative file path, attribute name): [line numbers]} for every
    `setattr(scheduler, "<publish|SessionLocal|_wait_for_messages|_wait_for_turn>", ...)`
    call under `root`/tests/test_*.py, however its arguments are line-wrapped."""
    hits: dict[tuple[str, str], list[int]] = {}
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return hits
    for path in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not _is_setattr_call(node) or len(node.args) < 2:
                continue
            target, attr_node = node.args[0], node.args[1]
            if not _is_bare_scheduler_name(target):
                continue
            if not (isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str)):
                continue
            attr = attr_node.value
            if attr not in PATCHED_ATTRS:
                continue
            key = (str(path.relative_to(root)), attr)
            hits.setdefault(key, []).append(node.lineno)
    return hits


def _recorded_scheduler_patches(root: pathlib.Path) -> dict[tuple[str, str], dict]:
    verdicts = _load_verdicts(root)
    return {(entry["file"], entry["name"]): entry for entry in verdicts.get("scheduler_patches", [])}


def find_unrecorded_or_returned(
    current: dict[tuple[str, str], list[int]], recorded: dict[tuple[str, str], dict]
) -> list[str]:
    """Current sites with no recorded entry, or whose recorded entry says
    "merged" (the patch was supposed to be gone)."""
    problems = []
    for (file, attr), lines in sorted(current.items()):
        entry = recorded.get((file, attr))
        for line in lines:
            if entry is None:
                problems.append(f'{file}:{line}: setattr(scheduler, "{attr}", ...) — {REDIRECT_MESSAGE}')
            elif entry["verdict"] == "merged":
                problems.append(
                    f'{file}:{line}: setattr(scheduler, "{attr}", ...) was merged; it is back — {REDIRECT_MESSAGE}'
                )
    return problems


def find_stale(
    current: dict[tuple[str, str], list[int]], recorded: dict[tuple[str, str], dict]
) -> list[str]:
    """Recorded "unjudged"/"leave" entries whose site no longer exists in
    that file. ("merged" entries are expected to be gone — that is not stale.)"""
    stale = []
    for (file, attr), entry in sorted(recorded.items()):
        if entry["verdict"] in ("unjudged", "leave") and (file, attr) not in current:
            stale.append(f"{file}: {attr}: recorded but no longer patched — delete the entry")
    return stale


def test_every_scheduler_patch_site_is_recorded() -> None:
    problems = find_unrecorded_or_returned(
        scan_scheduler_patches(REPO_ROOT), _recorded_scheduler_patches(REPO_ROOT)
    )
    assert not problems, "\n".join(problems)


def test_no_stale_scheduler_patch_entries() -> None:
    stale = find_stale(scan_scheduler_patches(REPO_ROOT), _recorded_scheduler_patches(REPO_ROOT))
    assert not stale, "\n".join(stale)


def test_new_scheduler_patch_fails_the_ratchet(tmp_path: pathlib.Path) -> None:
    """An unrecorded copy — even one whose call is wrapped across lines — must
    be reported, naming the file and the line the call starts on."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "def test_it(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        "        scheduler,\n"
        '        "publish",\n'
        "        noop,\n"
        "    )\n"
    )
    hits = scan_scheduler_patches(tmp_path)
    assert hits == {("tests/test_example.py", "publish"): [2]}

    problems = find_unrecorded_or_returned(hits, recorded={})
    assert len(problems) == 1
    assert "tests/test_example.py:2" in problems[0]
    assert REDIRECT_MESSAGE in problems[0]
